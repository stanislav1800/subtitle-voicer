"""``run``: voicing by profiles (stage 5). GameWatcher picks the active game, GameRunner
builds capture + zones from its profile, ProfileRouter turns utterances into speech requests
by the zone's policy and the profile's characters."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from subvoicer.app.container import (
    SpeechConfig,
    build_speech,
    load_ocr,
)
from subvoicer.app.runtime import MonotonicClock, Session
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile import MaskConfig, Profile, VoicingPolicy, Zone
from subvoicer.config.profile_store import ProfileStore, format_error
from subvoicer.config.resources import gender_exceptions, short_words
from subvoicer.config.settings import Settings
from subvoicer.core.events import CaptureStopped, GameActivated, GameClosed, UtteranceDetected
from subvoicer.core.models import Priority, Rect, SpeechRequest, Utterance, UtteranceKind, WindowRef
from subvoicer.core.ports import EventBus
from subvoicer.engines import registry
from subvoicer.pipeline.dedup import Deduplicator
from subvoicer.pipeline.games.matching import OfferPolicy
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner, OcrSettings
from subvoicer.pipeline.speaker.characters import CharacterBook
from subvoicer.pipeline.speaker.gender import GenderGuesser
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.speech.voices import VoiceDefaults, VoiceMode, VoicePicker
from subvoicer.pipeline.text.preparer import TextPreparer
from subvoicer.pipeline.text.pronunciation import PronunciationDict, PronunciationEntry
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec
from subvoicer.pipeline.vision.stability import StabilizationSettings
from subvoicer.services.capture_service import CaptureService
from subvoicer.services.game_watcher import GameWatcher
from subvoicer.services.speech_service import SpeechService
from subvoicer.services.zone_service import ZoneService
from subvoicer.sources.zone_ocr import NameSource, ZoneOcrSource, ZoneSpec

log = logging.getLogger(__name__)

CAPTURE_MODES = ("wgc", "dxgi")


# ---------------------------------------------------------------- profile -> pipeline objects


def mask_spec(config: MaskConfig) -> MaskSpec:
    return MaskSpec(
        MaskPreset(config.preset),
        colors_hsv=tuple(config.colors_hsv),
        threshold=config.threshold,
    )


def rect_of(zone: Zone) -> Rect:
    return Rect(*zone.rect)


def pronunciation(profile: Profile) -> PronunciationDict:
    return PronunciationDict(
        PronunciationEntry(rule.from_, rule.to, rule.regex) for rule in profile.pronunciation
    )


class OcrEngines:
    """OCR engines by id, loaded once and shared by all zones and sessions."""

    def __init__(self, options: Mapping[str, dict[str, Any]] | None = None) -> None:
        self._options = options or {"rapidocr": {"model": "cyrillic"}}
        self._engines: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, engine_id: str) -> Any:
        with self._lock:
            if engine_id not in self._engines:
                self._engines[engine_id] = load_ocr(
                    engine_id, dict(self._options.get(engine_id, {}))
                )
            return self._engines[engine_id]


def zone_specs(profile: Profile, aspect: str, engines: OcrEngines) -> list[ZoneSpec]:
    """``auto`` text zones of the layout; ``hotkey`` zones wait for stage 7, ``off`` are skipped."""
    layout = profile.layouts.get(aspect)
    if layout is None:
        return []
    by_id = {z.id: z for z in layout.zones}
    specs = []
    for zone in layout.zones:
        if zone.kind != "text" or zone.trigger != "auto" or zone.policy is None:
            continue
        speaker = zone.policy.speaker
        name: NameSource | None = None
        if speaker.mode == "zone" and speaker.zone_id:
            source = by_id[speaker.zone_id]
            name = NameSource(mask_spec(source.mask), rect_of(source))
        elif speaker.mode == "color":
            name = NameSource(mask_spec(MaskConfig(preset="color", colors_hsv=speaker.colors_hsv)))
        stab = profile.stabilization.merged(zone.stabilization)
        ocr = profile.ocr.merged(zone.ocr)
        specs.append(
            ZoneSpec(
                id=zone.id,
                rect=rect_of(zone),
                mask=mask_spec(zone.mask),
                speaker=SpeakerResolver(SpeakerMode(speaker.mode), profile.speaker_stoplist),
                stabilization=StabilizationSettings(
                    stable_ms=stab.stable_ms,
                    max_wait_ms=stab.max_wait_ms,
                    typing_max_wait_ms=stab.typing_max_wait_ms,
                    change_ratio=stab.change_ratio,
                    empty_ratio=stab.empty_ratio,
                ),
                repeat_cooldown_min=zone.policy.repeat_cooldown_min,
                name=name,
                runner=OcrRunner(engines.get(ocr.engine), OcrSettings(upscale=ocr.upscale)),
                junk=JunkFilter(short_words(), min_confidence=ocr.min_confidence),
            )
        )
    return specs


def capture_factories() -> dict[str, Any]:
    factories: dict[str, Any] = {}
    for mode in CAPTURE_MODES:
        cls = registry.resolve("capture", mode)
        if cls.is_available():
            factories[mode] = cls
    if "wgc" not in factories:
        raise RuntimeError("Захват окна (Windows Graphics Capture) недоступен")
    return factories


def build_game_session(
    profile: Profile,
    window: WindowRef,
    aspect: str,
    engines: OcrEngines,
    bus: EventBus,
    poll_hz: float,
) -> Session | None:
    from subvoicer.engines.system import windows

    specs = zone_specs(profile, aspect, engines)
    if not specs:
        return None
    clock = MonotonicClock()
    capture = CaptureService(window.hwnd, capture_factories(), clock, windows.is_window, bus)
    source = ZoneOcrSource(
        specs,
        OcrRunner(engines.get(profile.ocr.engine)),
        JunkFilter(short_words(), min_confidence=profile.ocr.min_confidence),
        Deduplicator(profile.stabilization.match_ratio),
        clock,
    )
    zones = ZoneService(capture.latest, source, bus, poll_hz)
    return Session([capture, zones])


# ---------------------------------------------------------------- utterance -> speech request


@dataclass(frozen=True)
class _Active:
    path: Path
    profile: Profile
    zones: dict[str, Zone]


class ProfileRouter:
    """Voice, priority and ducking by the zone's VoicingPolicy (TZ 4.2, 4.6); named speakers
    become characters of the profile, saved right away."""

    def __init__(self, store: ProfileStore, book: CharacterBook, gender: GenderGuesser) -> None:
        self._store = store
        self._book = book
        self._gender = gender
        self._lock = threading.Lock()
        self._active: _Active | None = None
        self._last_voice: dict[str, str] = {}

    def set_profile(self, path: Path | None, profile: Profile | None, aspect: str = "") -> None:
        with self._lock:
            if path is None or profile is None:
                self._active = None
                return
            layout = profile.layouts.get(aspect)
            zones = {z.id: z for z in layout.zones} if layout else {}
            self._active = _Active(path, profile, zones)

    def request(self, utterance: Utterance) -> SpeechRequest | None:
        with self._lock:
            active = self._active
            if active is None:
                return None
            zone = active.zones.get(utterance.channel)
            if zone is None or zone.policy is None:
                return None
            policy = zone.policy
            continuation = utterance.kind is UtteranceKind.CONTINUATION
            if continuation and utterance.channel in self._last_voice:
                voice = self._last_voice[utterance.channel]  # a continuation keeps the voice
            else:
                voice = self._voice(active, policy, utterance)
            self._last_voice[utterance.channel] = voice
        return SpeechRequest(
            id=utterance.id,
            text=utterance.text,
            voice_id=voice,
            priority=Priority(policy.priority),
            continuation=continuation,
            duck=policy.duck,
            channel=utterance.channel,
            speaker=utterance.speaker,
            t_change=utterance.t_change,
        )

    def _voice(self, active: _Active, policy: VoicingPolicy, utterance: Utterance) -> str:
        profile = active.profile
        picker = VoicePicker(
            VoiceDefaults(
                profile.narrator_voice, profile.default_voices.male, profile.default_voices.female
            ),
            self._gender,
        )
        mode = VoiceMode(policy.voice.mode)
        if mode is not VoiceMode.SPEAKER or not utterance.speaker:
            return picker.pick(mode, utterance.text, policy.voice.id)
        resolved = self._book.resolve(profile.characters, utterance.speaker, utterance.text)
        if resolved.changed:
            self._save_characters(active, utterance.speaker, utterance.text)
        return resolved.character.voice or picker.pick(mode, utterance.text)

    def _save_characters(self, active: _Active, name: str, text: str) -> None:
        """Applied to the file as it is on disk now, so hand edits are not lost."""

        def change(profile: Profile) -> Profile | None:
            resolved = self._book.resolve(profile.characters, name, text)
            if not resolved.changed:
                return None
            log.info(
                "Персонаж %s: пол %s, голос %s",
                resolved.character.name,
                resolved.character.gender,
                resolved.character.voice,
            )
            return profile.model_copy(update={"characters": resolved.characters})

        try:
            stored = self._store.update(active.path, change)
        except (OSError, ValueError) as exc:
            log.error("Персонажи не сохранены в %s: %s", active.path.name, format_error(exc))
            return
        self._active = _Active(active.path, stored.profile, active.zones)


# ---------------------------------------------------------------- switching games


class GameRunner:
    """Reacts to GameWatcher: one capture + zones session for the active game at a time."""

    def __init__(
        self,
        store: ProfileStore,
        router: ProfileRouter,
        speech: SpeechService,
        preparer: TextPreparer,
        build: Callable[[Profile, WindowRef, str], Session | None],
    ) -> None:
        self._store = store
        self._router = router
        self._speech = speech
        self._preparer = preparer
        self._build = build
        self._lock = threading.Lock()
        self._session: Session | None = None
        self._key: tuple[int, str, str] | None = None  # (pid, aspect, layouts json)

    def on_activated(self, event: GameActivated) -> None:
        path = Path(event.profile_path)
        try:
            profile = self._store.load(path)
        except (OSError, ValueError) as exc:
            log.error("Профиль %s не загружен: %s", path.name, format_error(exc))
            return
        layout = profile.layouts.get(event.aspect)
        key = (event.window.pid, event.aspect, layout.model_dump_json() if layout else "")
        with self._lock:
            self._router.set_profile(path, profile, event.aspect)
            self._preparer.dictionary = pronunciation(profile)
            self._speech.set_game(event.window.pid)
            if key == self._key and self._session is not None:
                return  # same game and zones: only voices/characters/dictionary changed
            self._stop_session()
            self._key = key
            if layout is None:
                log.warning(
                    "Профиль %s: нет зон для %s — озвучка не работает", path.name, event.aspect
                )
                return
            try:
                session = self._build(profile, event.window, event.aspect)
            except Exception:
                log.exception("Не удалось запустить зоны профиля %s", path.name)
                return
            if session is None:
                log.warning("Профиль %s: нет зон auto для %s", path.name, event.aspect)
                return
            session.start()
            self._session = session
            log.info("Зоны профиля %s запущены (%s)", path.name, event.aspect)

    def on_closed(self, _event: GameClosed | CaptureStopped) -> None:
        with self._lock:
            self._stop_session()
            self._key = None
            self._router.set_profile(None, None)
            self._speech.set_game(None)

    def start(self) -> None:
        """Sessions start when GameWatcher reports a game."""

    def stop(self) -> None:
        with self._lock:
            self._stop_session()

    def _stop_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.stop()


def speech_config(settings: Settings, hotkeys: bool = True) -> SpeechConfig:
    return SpeechConfig(
        base_rate=settings.base_rate,
        overlap=settings.overlap_mode,
        hurry_rate=settings.hurry_rate,
        max_lag_s=settings.max_lag_s,
        volume=settings.voice_volume,
        output_device=settings.output_device,
        hotkeys=hotkeys,
        low_priority_ttl_s=settings.low_priority_ttl_s,
        cache_size=settings.tts_cache_size,
        duck_level=settings.duck_level,
        duck_fade_ms=settings.duck_fade_ms,
        duck_release_ms=settings.duck_release_ms,
    )


def build_run(settings: Settings, bus: EventBus, paths: AppPaths, hotkeys: bool = True) -> Session:
    """Speech for the whole run, GameWatcher, and a game session rebuilt per active game."""
    from subvoicer.engines.system.windows import WindowsDesktop

    store = ProfileStore(paths.profiles_dir)
    speech = build_speech(speech_config(settings, hotkeys), bus, None, settings.hotkeys)
    gender = GenderGuesser(gender_exceptions())
    router = ProfileRouter(store, CharacterBook(speech.voices, gender), gender)
    engines = OcrEngines()
    engines.get("rapidocr")  # load before the first game, not on its first frame

    def build(profile: Profile, window: WindowRef, aspect: str) -> Session | None:
        return build_game_session(profile, window, aspect, engines, bus, settings.poll_hz)

    runner = GameRunner(store, router, speech.service, speech.preparer, build)
    service = speech.service

    def route(event: UtteranceDetected) -> None:
        request = router.request(event.utterance)
        if request is not None:
            service.submit(request)

    bus.subscribe(UtteranceDetected, route)
    bus.subscribe(GameActivated, runner.on_activated)
    bus.subscribe(GameClosed, runner.on_closed)
    bus.subscribe(CaptureStopped, runner.on_closed)
    watcher = GameWatcher(
        store,
        WindowsDesktop(),
        OfferPolicy(settings.excluded_exes, settings.never_ask_exes),
        bus,
    )
    return Session([speech.session, watcher, runner])  # stopped in reverse: game, watcher, speech
