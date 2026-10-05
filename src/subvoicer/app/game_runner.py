"""``run``: voicing by profiles (stage 5). GameWatcher picks the active game, GameRunner
builds capture + zones from its profile, ProfileRouter turns utterances into speech requests
by the zone's policy and the profile's characters."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from subvoicer.app.container import (
    SpeechConfig,
    SpeechParts,
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
from subvoicer.sources.manual import SELECT_CHANNEL
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
        self._options = options or {"rapidocr": {"model": "eslav"}}
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
    """``auto`` text zones of the layout; ``hotkey`` zones are read by their key (ManualReader),
    ``off`` are skipped."""
    layout = profile.layouts.get(aspect)
    if layout is None:
        return []
    zones = layout.zones
    return [
        zone_spec(profile, zone, zones, engines)
        for zone in zones
        if zone.kind == "text" and zone.trigger == "auto" and zone.policy is not None
    ]


def zone_spec(profile: Profile, zone: Zone, zones: list[Zone], engines: OcrEngines) -> ZoneSpec:
    """One text zone of a profile as the pipeline sees it (speaker zones are looked up in
    ``zones``)."""
    assert zone.policy is not None
    by_id = {z.id: z for z in zones}
    speaker = zone.policy.speaker
    name: NameSource | None = None
    if speaker.mode == "zone" and speaker.zone_id in by_id:
        source = by_id[speaker.zone_id]
        name = NameSource(mask_spec(source.mask), rect_of(source))
    elif speaker.mode == "color":
        name = NameSource(mask_spec(MaskConfig(preset="color", colors_hsv=speaker.colors_hsv)))
    stab = profile.stabilization.merged(zone.stabilization)
    ocr = profile.ocr.merged(zone.ocr)
    return ZoneSpec(
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


def capture_factories() -> dict[str, Any]:
    factories: dict[str, Any] = {}
    for mode in CAPTURE_MODES:
        cls = registry.resolve("capture", mode)
        if cls.is_available():
            factories[mode] = cls
    if "wgc" not in factories:
        raise RuntimeError("Захват окна (Windows Graphics Capture) недоступен")
    return factories


class GameSession(Session):
    """Capture + zones of the active game; the overlay and Ctrl+Alt+D look inside."""

    def __init__(self, capture: CaptureService, zones: ZoneService, source: ZoneOcrSource) -> None:
        super().__init__([capture, zones])
        self.capture = capture
        self.source = source


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
    return GameSession(capture, zones, source)


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
                reason = "продолжение — голос прежней строки"
            else:
                voice, reason = self._voice(active, policy, utterance)
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
            voice_reason=reason,
            zone_name=zone.name or zone.id,
        )

    def manual(self, utterances: list[Utterance]) -> list[SpeechRequest]:
        """A hotkey zone or a selection read on request (TZ 4.9): manual priority; the next
        speakers of the same reading follow the first one instead of cutting it."""
        requests: list[SpeechRequest] = []
        for utterance in utterances:
            request = (
                self._selection(utterance)
                if utterance.channel == SELECT_CHANNEL
                else self.request(utterance)
            )
            if request is not None:
                requests.append(
                    replace(request, priority=Priority.MANUAL, continuation=bool(requests))
                )
        return requests

    def _selection(self, utterance: Utterance) -> SpeechRequest | None:
        with self._lock:
            active = self._active
        if active is None:
            return None
        return SpeechRequest(
            id=utterance.id,
            text=utterance.text,
            voice_id=active.profile.narrator_voice,
            priority=Priority.MANUAL,
            channel=utterance.channel,
            t_change=utterance.t_change,
            voice_reason="выделение — рассказчик",
            zone_name="Выделение",
        )

    def zone(self, zone_id: str) -> tuple[Profile, Zone, list[Zone]] | None:
        """A zone of the active layout with its profile and neighbours (for manual reading)."""
        with self._lock:
            active = self._active
        if active is None or zone_id not in active.zones:
            return None
        return active.profile, active.zones[zone_id], list(active.zones.values())

    def profile(self) -> Profile | None:
        with self._lock:
            return self._active.profile if self._active is not None else None

    def _voice(
        self, active: _Active, policy: VoicingPolicy, utterance: Utterance
    ) -> tuple[str, str]:
        """The voice and why it was chosen (developer History, TZ 4.13.2)."""
        profile = active.profile
        picker = VoicePicker(
            VoiceDefaults(
                profile.narrator_voice, profile.default_voices.male, profile.default_voices.female
            ),
            self._gender,
        )
        mode = VoiceMode(policy.voice.mode)
        if mode is VoiceMode.FIXED:
            return picker.pick(mode, utterance.text, policy.voice.id), "голос зоны"
        if mode is VoiceMode.NARRATOR:
            return picker.pick(mode, utterance.text), "рассказчик зоны"
        if not utterance.speaker:
            gender = self._gender.guess(utterance.text)
            why = {"male": "мужской по тексту", "female": "женский по тексту"}.get(
                gender.value, "рассказчик: имени и пола нет"
            )
            return picker.pick(mode, utterance.text), why
        resolved = self._book.resolve(profile.characters, utterance.speaker, utterance.text)
        if resolved.changed:
            self._save_characters(active, utterance.speaker, utterance.text)
        character = resolved.character
        how = "вручную" if character.voice_locked else "авто"
        matched = "" if character.name == utterance.speaker else f"«{utterance.speaker}» → "
        if character.voice:
            return character.voice, f"{matched}персонаж {character.name}, {how}"
        return picker.pick(mode, utterance.text), f"{matched}персонаж {character.name} без голоса"

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
        build: Callable[[Profile, WindowRef, str, float], Session | None],
        poll_hz: float = 10,
    ) -> None:
        self._store = store
        self._router = router
        self._speech = speech
        self._preparer = preparer
        self._build = build
        self.poll_hz = poll_hz
        self._last: GameActivated | None = None
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
            self._last = event
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
                session = self._build(profile, event.window, event.aspect, self.poll_hz)
            except Exception:
                log.exception("Не удалось запустить зоны профиля %s", path.name)
                return
            if session is None:
                log.warning("Профиль %s: нет зон auto для %s", path.name, event.aspect)
                return
            session.start()
            self._session = session
            log.info("Зоны профиля %s запущены (%s)", path.name, event.aspect)

    def set_poll_hz(self, poll_hz: float) -> None:
        """A new zone polling rate: the running game session is rebuilt with it."""
        with self._lock:
            if poll_hz == self.poll_hz:
                return
            self.poll_hz = poll_hz
            last, self._key = self._last, None
            self._stop_session()
        if last is not None:
            self.on_activated(last)

    def on_closed(self, _event: GameClosed | CaptureStopped) -> None:
        with self._lock:
            self._last = None
            self._stop_session()
            self._key = None
            self._router.set_profile(None, None)
            self._speech.set_game(None)

    def zone_states(self) -> dict[str, str]:
        session = self._session
        return session.source.states() if isinstance(session, GameSession) else {}

    def latest_frame(self) -> Any:
        session = self._session
        frame = session.capture.latest() if isinstance(session, GameSession) else None
        return None if frame is None else frame.image

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


@dataclass
class RunParts:
    """Everything ``run`` is made of; the GUI facade reaches the parts to apply settings."""

    session: Session
    speech: SpeechParts
    router: ProfileRouter
    runner: GameRunner
    watcher: GameWatcher
    offers: OfferPolicy
    store: ProfileStore
    engines: OcrEngines


def build_run(settings: Settings, bus: EventBus, paths: AppPaths, hotkeys: bool = True) -> RunParts:
    """Speech for the whole run, GameWatcher, and a game session rebuilt per active game."""
    from subvoicer.engines.system.windows import WindowsDesktop

    store = ProfileStore(paths.profiles_dir)
    speech = build_speech(speech_config(settings, hotkeys), bus, None, settings.hotkeys)
    gender = GenderGuesser(gender_exceptions())
    router = ProfileRouter(store, CharacterBook(speech.voices, gender), gender)
    engines = OcrEngines()
    engines.get("rapidocr")  # load before the first game, not on its first frame

    def build(profile: Profile, window: WindowRef, aspect: str, poll_hz: float) -> Session | None:
        return build_game_session(profile, window, aspect, engines, bus, poll_hz)

    runner = GameRunner(
        store, router, speech.service, speech.preparer, build, poll_hz=settings.poll_hz
    )
    service = speech.service

    def route(event: UtteranceDetected) -> None:
        request = router.request(event.utterance)
        if request is not None:
            service.submit(request)

    bus.subscribe(UtteranceDetected, route)
    bus.subscribe(GameActivated, runner.on_activated)
    bus.subscribe(GameClosed, runner.on_closed)
    bus.subscribe(CaptureStopped, runner.on_closed)
    offers = OfferPolicy(settings.excluded_exes, settings.never_ask_exes)
    watcher = GameWatcher(store, WindowsDesktop(), offers, bus)
    session = Session(
        [speech.session, watcher, runner]
    )  # stopped in reverse: game, watcher, speech
    return RunParts(session, speech, router, runner, watcher, offers, store, engines)
