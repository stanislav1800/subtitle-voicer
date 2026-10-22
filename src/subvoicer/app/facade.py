"""AppFacade: what the GUI may do (TZ 4.13). The UI talks only to this, config and core.

Owns the ``run`` parts (speech, GameWatcher, game sessions), the history of recent lines and
the current settings; every call is safe from the Qt thread.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from subvoicer.app import game_runner, library, runtime, zone_tools
from subvoicer.app import profile_commands as pc
from subvoicer.app.bus import Bus
from subvoicer.app.character_setup import CharacterSetup
from subvoicer.app.container import installed_character_models
from subvoicer.app.game_runner import RunParts, build_run, pronunciation
from subvoicer.app.log import set_zone_details
from subvoicer.app.models_setup import Progress
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile import Layout, Profile, Zone
from subvoicer.config.profile_store import ProfileStore, StoredProfile
from subvoicer.config.resources import (
    character_voices,
    gender_exceptions,
    short_words,
    voice_catalog,
)
from subvoicer.config.settings import Settings, load_settings, save_settings
from subvoicer.core.events import (
    DebugCaptured,
    Event,
    GameActivated,
    GameClosed,
    LayoutMissing,
    LibraryScanned,
    SettingsApplied,
)
from subvoicer.core.models import (
    CharacterVoice,
    HistoryEntry,
    Priority,
    Rect,
    SpeechRequest,
    Voice,
    WindowRef,
)
from subvoicer.pipeline.games.library import folder_key
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner, OcrSettings
from subvoicer.pipeline.speaker.gender import GenderGuesser
from subvoicer.pipeline.speech.scheduler import OverlapMode
from subvoicer.pipeline.speech.voices import by_preference
from subvoicer.services.history import History
from subvoicer.services.speech_service import SpeechTuning
from subvoicer.sources.manual import ManualReader, selection_spec
from subvoicer.sources.zone_ocr import ZoneSpec, read_once

log = logging.getLogger(__name__)

MANUAL_ID_START = 2_000_000  # ids of manual lines, apart from zone lines and repeats
FREEZE_WATCHDOG_S = 3.0  # a paused game goes on if the interface is stuck this long (TZ 1.30)


@dataclass(frozen=True, slots=True)
class OverlayZone:
    id: str
    name: str
    rect: tuple[float, float, float, float]
    state: str
    plates: tuple[tuple[float, float, float, float], ...] = ()  # TZ 4.2.2, frame fractions


@dataclass(frozen=True, slots=True)
class DebugState:
    """What the developer overlay shows (TZ 4.13.2)."""

    status: GameStatus
    client_rect: tuple[int, int, int, int] | None  # physical screen pixels of the game image
    monitor: str | None  # device name of the game's monitor
    monitor_rect: tuple[int, int, int, int] | None  # physical pixels
    zones: list[OverlayZone]
    rate: float
    playing: str  # speaker and voice of the line that sounds now, or ""
    playing_text: str
    last: HistoryEntry | None


@dataclass(frozen=True, slots=True)
class CharacterRow:
    """A character voice in the list of «Общие» (TZ 1.24)."""

    model: str
    name: str
    game: str
    base: str
    installed: bool
    download: int  # bytes
    disk: int  # bytes on disk (expected before the install)


@dataclass(frozen=True, slots=True)
class CharacterHardware:
    """What the character voices can run on (TZ 1.25)."""

    nvidia: str | None  # the NVIDIA GPU that CUDA can use
    radeon: str | None  # the Radeon that torch for ROCm supports (RX 7700 and newer, Windows 11)
    others: tuple[str, ...]  # other adapters Windows knows (older Radeon, Intel, built-in)

    def devices(self) -> list[str]:
        """What the engine can be built for here, the best first."""
        from subvoicer.app.character_setup import CPU, CUDA, ROCM

        return [d for d, gpu in ((CUDA, self.nvidia), (ROCM, self.radeon)) if gpu] + [CPU]


@dataclass(frozen=True, slots=True)
class GameStatus:
    """What the tray and the settings sidebar show."""

    enabled: bool
    exe: str | None = None
    title: str | None = None
    profile_path: str | None = None
    aspect: str | None = None
    window: WindowRef | None = None
    has_zones: bool = False


class AppFacade:
    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths.default()
        self.bus = Bus()
        self.settings: Settings = load_settings(self.paths.settings_file)
        self.store = ProfileStore(self.paths.profiles_dir)
        self.history = History(self.settings.history_size)
        self.history.attach(self.bus)
        self._parts: RunParts | None = None
        self._lock = threading.Lock()
        self._game: GameActivated | None = None
        self._missing = False
        self._title: str | None = None
        self._manual_ids = itertools.count(MANUAL_ID_START)
        self._hotkeys: object | None = None
        self._hotkey_actions: dict[str, Callable[[], None]] = {
            "pause": self.toggle_enabled,
            "repeat": self.repeat,
            "skip": self.skip,
            "debug_capture": self._capture_in_background,
        }
        self._zone_keys: dict[str, str] = {}  # hotkey zones of the active layout: combo -> id
        # a game a crashed run left paused by the zone editor goes on first (TZ 1.30)
        self._freezer = runtime.make_freezer(self.paths.frozen_games_file)
        self._freezer.thaw_all()
        self._frozen: int | None = None
        self._freeze_lock = threading.Lock()
        self._ui_beat = 0.0
        self.freeze_watchdog_s = FREEZE_WATCHDOG_S
        self._reader: ManualReader | None = None
        self._library_lock = threading.Lock()
        self._hardware: CharacterHardware | None = None
        self._engine_sizes: dict[tuple[bool, str], int] = {}  # (installed, device) -> bytes
        self._engine_lock = threading.Lock()
        self._device: str | None = None  # what to build the engine for; None — the best one
        self._installing: CharacterSetup | None = None
        set_zone_details(self.settings.dev_mode)
        self.bus.subscribe(GameActivated, self._on_activated)
        self.bus.subscribe(LayoutMissing, self._on_missing)
        self.bus.subscribe(GameClosed, self._on_closed)

    # ------------------------------------------------------------------ life cycle

    def start(self, hotkeys: bool = True) -> None:
        """Loads OCR and TTS (seconds) — call from a worker thread, not the Qt one."""
        parts = build_run(self.settings, self.bus, self.paths, hotkeys=False)
        parts.session.start()
        parts.speech.service.set_paused(not self.settings.enabled)
        self._reader = ManualReader(
            OcrRunner(parts.engines.get("rapidocr")),
            JunkFilter(short_words()),
            runtime.MonotonicClock(),
        )
        self._parts = parts
        if hotkeys:
            self._hotkeys = runtime.make_hotkeys()
            self._register_hotkeys()

    def stop(self) -> None:
        self.thaw_game()
        hotkeys, self._hotkeys = self._hotkeys, None
        stop_hotkeys = getattr(hotkeys, "stop", None)
        if stop_hotkeys is not None:
            stop_hotkeys()
        parts, self._parts = self._parts, None
        if parts is not None:
            parts.session.stop()

    # ------------------------------------------------------------------ game pause

    def freeze_game(self) -> bool | None:
        """The zone editor opens over the game (TZ 4.13, 1.30): the game is paused, its
        capture stops. True — paused; False — Windows refused (an anti-cheat); None — the
        profile asks not to pause or there is no game."""
        status = self.status()
        if status.window is None or status.profile_path is None:
            return None
        try:
            profile = self.store.load(Path(status.profile_path))
        except (OSError, ValueError):
            return None
        if not profile.pause_in_editor:
            return None
        pid = status.window.pid
        with self._freeze_lock:
            if self._frozen is not None:
                return True
            if not self._freezer.freeze(pid):
                return False
            self._frozen = pid
            self._ui_beat = time.monotonic()
        parts = self._parts
        if parts is not None:
            parts.runner.hold(True)
        threading.Thread(
            target=self._watch_frozen, args=(pid,), name="FreezeWatchdog", daemon=True
        ).start()
        return True

    def ui_alive(self) -> None:
        """The interface is responsive (a timer in the UI thread): the paused game may wait."""
        self._ui_beat = time.monotonic()

    def _watch_frozen(self, pid: int) -> None:
        """A safety net: a window shown over a paused game may wait forever for an answer
        from it (Windows sends it messages), so a stuck interface lets the game go on."""
        while self._frozen == pid:
            time.sleep(0.25)
            if self._frozen == pid and time.monotonic() - self._ui_beat > self.freeze_watchdog_s:
                log.warning("Интерфейс не отвечает — игра снята с паузы")
                self.thaw_game()
                return

    def thaw_game(self) -> None:
        """The zone editor closes: the game goes on, its capture starts again."""
        with self._freeze_lock:
            pid, self._frozen = self._frozen, None
            if pid is None:
                return
            self._freezer.thaw(pid)
        parts = self._parts
        if parts is not None:
            parts.runner.hold(False)

    # ------------------------------------------------------------------ settings

    def apply_settings(self, new: Settings) -> None:
        """Save and apply at once, without a restart (TZ 4.14)."""
        old, self.settings = self.settings, new
        save_settings(self.paths.settings_file, new)
        self.history.resize(new.history_size)
        if new.autostart != old.autostart:
            try:
                runtime.set_autostart(new.autostart)
            except OSError:
                log.exception("Не удалось изменить автозапуск")
        parts = self._parts
        if parts is not None:
            parts.speech.service.tune(tuning(new))
            parts.speech.service.set_paused(not new.enabled)
            parts.offers.update(new.excluded_exes, new.never_ask_exes)
            parts.runner.set_poll_hz(new.poll_hz)
            if parts.speech.characters is not None:
                parts.speech.characters.set_enabled(new.character_voices)
        if new.hotkeys != old.hotkeys or new.dev_mode != old.dev_mode:
            self._register_hotkeys()
        set_zone_details(new.dev_mode)
        self.bus.publish(SettingsApplied())

    def _capture_in_background(self) -> None:
        threading.Thread(target=self.debug_capture, name="DebugCapture", daemon=True).start()

    def set_hotkey_action(self, name: str, action: Callable[[], None]) -> None:
        """Actions the GUI owns (zone editor, debug frame): bound with the others."""
        self._hotkey_actions[name] = action
        self._register_hotkeys()

    def _register_hotkeys(self) -> None:
        start = getattr(self._hotkeys, "start", None)
        if start is None:
            return
        actions = dict(self._hotkey_actions)
        if not self.settings.dev_mode:
            actions.pop("debug_capture", None)  # TZ 4.13.2: not registered without dev mode
        bindings = {
            self.settings.hotkeys[name]: action
            for name, action in actions.items()
            if name in self.settings.hotkeys
        }
        for combo, zone_id in self._zone_keys.items():
            if combo in bindings:
                log.warning("Клавиша %s зоны %s занята общим действием", combo, zone_id)
                continue
            bindings[combo] = lambda zone_id=zone_id: self.read_zone(zone_id)
        try:
            start(bindings)
        except Exception:
            log.exception("Горячие клавиши не зарегистрированы")

    @property
    def ready(self) -> bool:
        return self._parts is not None

    def subscribe(self, event_type: type[Event], handler: Callable[[Event], None]) -> None:
        self.bus.subscribe(event_type, handler)

    # ------------------------------------------------------------------ speech

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self.settings.enabled:
            self.apply_settings(self.settings.model_copy(update={"enabled": enabled}))

    def toggle_enabled(self) -> None:
        self.set_enabled(not self.settings.enabled)

    def repeat(self) -> None:
        if self._parts is not None:
            self._parts.speech.service.repeat()

    def skip(self) -> None:
        if self._parts is not None:
            self._parts.speech.service.skip()

    def say(
        self,
        text: str,
        voice_id: str,
        speaker: str | None = None,
        pronunciation: tuple[tuple[str, str, bool], ...] = (),
    ) -> None:
        """A manual line (TZ 4.9): cuts what plays and pauses auto voicing until it ends."""
        if self._parts is None or not text.strip():
            return
        request = SpeechRequest(
            id=next(self._manual_ids),
            text=text,
            voice_id=voice_id,
            priority=Priority.MANUAL,
            channel="manual",
            speaker=speaker,
            pronunciation=pronunciation,
        )
        self._parts.speech.service.submit(request)

    # ------------------------------------------------------------------ manual reading

    def read_zone(self, zone_id: str) -> None:
        """A ``hotkey`` zone of the active game, read once (TZ 4.9). Blocks for the OCR."""
        parts = self._parts
        found = parts.router.zone(zone_id) if parts is not None else None
        if parts is None or found is None:
            log.warning("Зона %s не прочитана: нет активной игры с этой зоной", zone_id)
            return
        profile, zone, zones = found
        if zone.kind != "text" or zone.policy is None:
            return
        frame = self.game_frame()
        if frame is None:
            log.warning("Зона %s не прочитана: нет кадра игры", zone_id)
            return
        self._read(game_runner.zone_spec(profile, zone, zones, parts.engines), frame)

    def read_area(self, frame: zone_tools.Image, rect: tuple[float, float, float, float]) -> None:
        """«Выделить и прочитать» (Ctrl+Alt+A): ``rect`` in fractions of ``frame``."""
        parts = self._parts
        profile = parts.router.profile() if parts is not None else None
        if parts is None or profile is None:
            return
        ocr = profile.ocr
        spec = replace(
            selection_spec(Rect(*rect), profile.speaker_stoplist),
            runner=OcrRunner(parts.engines.get(ocr.engine), OcrSettings(upscale=ocr.upscale)),
            junk=JunkFilter(short_words(), min_confidence=ocr.min_confidence),
        )
        self._read(spec, frame)

    def _read(self, spec: ZoneSpec, frame: zone_tools.Image) -> None:
        parts, reader = self._parts, self._reader
        if parts is None or reader is None:
            return
        try:
            utterances = reader.read(spec, frame)
        except Exception:
            log.exception("Зона %s не прочитана", spec.id)
            return
        for request in parts.router.manual(utterances):
            parts.speech.service.submit(request)

    def game_frame(self) -> zone_tools.Image | None:
        """The latest frame of the running capture, or a fresh one (no ``auto`` zones)."""
        parts = self._parts
        frame = parts.runner.latest_frame() if parts is not None else None
        if frame is not None:
            return np.ascontiguousarray(frame[:, :, :3])
        return self.grab_game_frame()

    def zone_hotkeys(self) -> dict[str, str]:
        """Hotkeys of hotkey zones in all profiles: combo -> «game · zone» (TZ 4.12)."""
        found: dict[str, str] = {}
        for stored in self.store.load_all():
            for zone in stored.profile.layout.zones:
                if zone.trigger == "hotkey" and zone.hotkey:
                    title = stored.profile.title or stored.path.stem
                    found[zone.hotkey] = f"{title} · {zone.name or zone.id}"
        return found

    def output_devices(self) -> list[tuple[str, str]]:
        """(stored name, name to show)."""
        return runtime.output_devices()

    def voices(self) -> list[Voice]:
        """Voices of the loaded engine; before it loads, the catalog (same ids for Silero)."""
        parts = self._parts
        if parts is not None:
            source = parts.speech.characters
            return by_preference(source.voices() if source is not None else parts.speech.voices)
        return by_preference(list(voice_catalog(pc.TTS_CATALOG).values()))

    def characters_installed(self) -> bool:
        """At least one character voice and the engine are installed (stage 16, TZ 1.24)."""
        return bool(installed_character_models(self._character_setup()))

    # ------------------------------------------------------------------ character voices (TZ 1.24)

    def character_hardware(self) -> CharacterHardware:
        """The GPUs of the computer (asked once)."""
        if self._hardware is None:
            from subvoicer.engines.system.gpu import nvidia_gpu, rocm_gpu, video_adapters

            adapters = video_adapters()
            nvidia = nvidia_gpu()
            radeon = rocm_gpu(adapters)
            others = tuple(
                a.name
                for a in adapters
                if a.name != radeon and (a.vendor != "nvidia" or not nvidia)
            )
            self._hardware = CharacterHardware(nvidia, radeon, others)
            log.info(
                "Видеокарты: NVIDIA %s, Radeon для ROCm %s, прочие: %s",
                nvidia or "нет",
                radeon or "нет",
                ", ".join(others) or "нет",
            )
        return self._hardware

    @property
    def character_device(self) -> str:
        """What the engine is built for when it is installed next: "cuda", "rocm" or "cpu"."""
        if self._device is not None:
            return self._device
        return self.character_hardware().devices()[0]

    @character_device.setter
    def character_device(self, device: str) -> None:
        self._device = device

    def character_rows(self) -> list[CharacterRow]:
        setup = self._character_setup()
        by_model: dict[str, list[CharacterVoice]] = {}
        for voice in character_voices():
            by_model.setdefault(voice.model, []).append(voice)
        rows = []
        for model in setup.voices():
            voices = by_model.get(model, [])
            package = setup.package(model)
            rows.append(
                CharacterRow(
                    model=model,
                    name=", ".join(v.name for v in voices) or model,
                    game=voices[0].game if voices else "",
                    base=voices[0].base if voices else "",
                    installed=package.installed,
                    download=package.download,
                    disk=package.disk,
                )
            )
        return rows

    def character_engine(self) -> tuple[bool, int, str | None]:
        """(installed, bytes on disk — or to be, what it is built for) of the shared engine."""
        setup = self._character_setup(self.character_device)
        installed = setup.engine_installed()
        return installed, self._engine_size(setup, installed), setup.env_device()

    def _engine_size(self, setup: CharacterSetup, installed: bool) -> int:
        """-1 while it is being counted. The installed engine is counted file by file (~8 s
        for its ~2 GB) in the background, once — «Общие» showed after a long freeze each time;
        installing or removing anything counts it again."""
        if not installed:
            return setup.engine_size()  # an estimate from the manifest, instant
        key = (installed, self.character_device)
        with self._engine_lock:
            if key in self._engine_sizes:
                return self._engine_sizes[key]
            self._engine_sizes[key] = -1

        def count() -> None:
            size = setup.engine_size()
            with self._engine_lock:
                if self._engine_sizes.get(key) == -1:
                    self._engine_sizes[key] = size

        threading.Thread(target=count, name="EngineSize", daemon=True).start()
        return -1

    def install_character(
        self,
        model: str,
        on_progress: Callable[[str, float | None], None],
        on_done: Callable[[str | None], None],
    ) -> None:
        """Download a voice in the background (the engine must be there, TZ 1.25).
        ``on_progress(text, fraction or None while it cannot be measured)``, ``on_done(error or
        None)`` — from the worker thread."""
        setup = self._character_setup()
        self._run_install(
            setup, lambda say, report: setup.install(model, say, report), on_progress, on_done
        )

    def install_character_engine(
        self,
        on_progress: Callable[[str, float | None], None],
        on_done: Callable[[str | None], None],
    ) -> None:
        """Install the engine for ``character_device`` in the background (TZ 1.25)."""
        setup = self._character_setup(self.character_device)
        self._run_install(setup, setup.install_engine, on_progress, on_done)

    def _run_install(
        self,
        setup: CharacterSetup,
        job: Callable[[Callable[[str], None], Callable[[Progress], None]], None],
        on_progress: Callable[[str, float | None], None],
        on_done: Callable[[str | None], None],
    ) -> None:
        from subvoicer.app.character_setup import SetupCancelled, SetupError

        self._installing = setup
        state = {"text": ""}

        def say(text: str) -> None:
            state["text"] = text
            on_progress(text, None)

        def report(p: Progress) -> None:
            if p.total >= 2**30:  # the engine's packages: gigabytes, the total is a guess
                done = f"{p.done / 2**30:.1f} из ~{p.total / 2**30:.1f} ГБ".replace(".", ",")
            else:
                done = f"{p.done / 2**20:.0f} из {p.total / 2**20:.0f} МБ"
            on_progress(f"{state['text']} {done}", p.fraction)

        def work() -> None:
            error: str | None = None
            try:
                job(say, report)
            except SetupCancelled:
                error = "Отменено — скачанное продолжится со следующего раза"
            except SetupError as exc:
                error = str(exc)
            except Exception as exc:
                log.exception("Голос персонажа или движок не установился")
                error = f"Не установилось: {exc}"
            finally:
                self._installing = None
            self._refresh_characters()
            on_done(error)

        threading.Thread(target=work, name="CharacterInstall", daemon=True).start()

    def cancel_character_install(self) -> None:
        setup = self._installing
        if setup is not None:
            setup.cancel()

    def characters_using(self, model: str) -> list[str]:
        """Characters and profiles that have a voice of this model (to warn before removing)."""
        ids = {v.id for v in character_voices() if v.model == model}
        found = []
        for stored in self.store.load_all():
            profile = stored.profile
            title = profile.title or stored.path.stem
            found += [f"{c.name} ({title})" for c in profile.characters if c.voice in ids]
            voices = [
                profile.narrator_voice,
                profile.default_voices.male,
                profile.default_voices.female,
            ]
            found += [f"голос по умолчанию ({title})" for v in voices if v in ids]
        return found

    def remove_character(self, model: str) -> None:
        self._character_setup().remove(model)
        self._refresh_characters()

    def remove_character_engine(self) -> None:
        parts = self._parts
        if parts is not None and parts.speech.characters is not None:
            parts.speech.characters.set_installed([])  # stops the helper before its files go
            parts.speech.characters.close()
        self._character_setup().remove_engine()
        self._refresh_characters()

    def _character_setup(self, device: str | None = None) -> CharacterSetup:
        """``device`` matters only for an engine not built yet (its size, the install)."""
        if device is None:
            return CharacterSetup(self.paths)
        return CharacterSetup(self.paths, device=device)

    def _refresh_characters(self) -> None:
        with self._engine_lock:
            self._engine_sizes.clear()  # something was installed or removed
        parts = self._parts
        if parts is not None and parts.speech.characters is not None:
            parts.speech.characters.set_installed(
                installed_character_models(self._character_setup())
            )
        self.bus.publish(SettingsApplied())  # the lists of voices changed

    # ------------------------------------------------------------------ developer mode

    def debug_state(self) -> DebugState:
        status = self.status()
        parts = self._parts
        zones: list[OverlayZone] = []
        client = monitor = monitor_rect = None
        rate, playing, playing_text = 1.0, "", ""
        if status.window is not None:
            client = runtime.client_rect(status.window.hwnd)
            monitor = runtime.monitor_device(status.window.hwnd)
            monitor_rect = runtime.monitor_rect(status.window.hwnd)
        if parts is not None:
            states = parts.runner.zone_states()
            plates = parts.runner.zone_plates()
            layout = self._active_layout(status)
            for zone in layout.zones if layout else []:
                state = states.get(zone.id, "OFF" if zone.trigger == "off" else "—")
                found = tuple(plates.get(zone.id, ()))
                zones.append(OverlayZone(zone.id, zone.name or zone.id, zone.rect, state, found))
            scheduler = parts.speech.service.scheduler
            rate = scheduler.current_rate
            current = scheduler.current
            if current is not None:
                playing = f"{current.speaker or 'Рассказчик'} · {current.voice_id}"
                playing_text = current.text
        recent = self.history.recent(1)
        last = recent[0] if recent else None
        return DebugState(
            status, client, monitor, monitor_rect, zones, rate, playing, playing_text, last
        )

    def _active_layout(self, status: GameStatus) -> Layout | None:
        if not status.profile_path:
            return None
        try:
            return self.store.load(Path(status.profile_path)).layout
        except (OSError, ValueError):
            return None

    def debug_capture(self) -> Path | None:
        """Ctrl+Alt+D: the full frame, crops and masks of every zone, OCR results as JSON."""
        status = self.status()
        if not self.settings.dev_mode or status.window is None or status.profile_path is None:
            return None
        parts = self._parts
        frame = parts.runner.latest_frame() if parts is not None else None
        if frame is None:
            frame = zone_tools.grab_window(status.window.hwnd)
        if frame is None:
            return None
        folder = self.paths.captures_dir / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder.mkdir(parents=True, exist_ok=True)
        frame = np.ascontiguousarray(frame[:, :, :3])
        zone_tools.save_image(folder / "frame.png", frame)
        profile = self.store.load(Path(status.profile_path))
        layout = profile.layout
        zones: dict[str, object] = {}
        for zone in layout.zones if layout else []:
            if zone.kind != "text" or zone.policy is None or parts is None or layout is None:
                continue
            spec = game_runner.zone_spec(profile, zone, layout.zones, parts.engines)
            assert spec.runner is not None
            reading = read_once(spec, frame, spec.runner)
            zone_tools.save_image(folder / f"{zone.id}_crop.png", reading.crop)
            zone_tools.save_image(folder / f"{zone.id}_mask.png", reading.mask)
            zones[zone.id] = {
                "rect": list(zone.rect),
                "text": reading.text,
                "plates": [list(box) for box in reading.plates],  # TZ 4.2.2, crop pixels
                "confidence": round(reading.confidence, 3),
                "input": reading.input_kind,
                "ms": round(reading.elapsed_ms),
                "lines": [{"speaker": line.name, "text": line.text} for line in reading.lines],
            }
        results = {
            "profile": Path(status.profile_path).name,
            "aspect": status.aspect,
            "exe": status.exe,
            "zones": zones,
        }
        (folder / "ocr.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Кадр для отладки сохранён: %s", folder)
        self.bus.publish(DebugCaptured(str(folder)))
        return folder

    # ------------------------------------------------------------------ zone editor

    def grab_game_frame(self) -> zone_tools.Image | None:
        """A frozen frame of the active game for the editor «поверх игры»."""
        window = self.status().window
        return zone_tools.grab_window(window.hwnd) if window is not None else None

    def game_monitor(self) -> tuple[int, int, int, int] | None:
        """The game's monitor, physical pixels."""
        window = self.status().window
        return runtime.monitor_rect(window.hwnd) if window is not None else None

    def preview_zone(
        self, frame: zone_tools.Image, zone: Zone, zones: list[Zone], profile: Profile
    ) -> zone_tools.ZonePreview:
        if self._parts is None:
            return zone_tools.ZonePreview(None, error="OCR ещё загружается")
        return zone_tools.preview_zone(
            frame,
            zone,
            zones,
            profile,
            self._parts.engines,
            self.voices(),
            GenderGuesser(gender_exceptions()),
        )

    # ------------------------------------------------------------------ profiles

    def profiles(self) -> list[StoredProfile]:
        return self.store.load_all()

    def profile(self, path: Path) -> Profile:
        return self.store.load(path)

    def update_profile(
        self, path: Path, change: Callable[[Profile], Profile | None]
    ) -> StoredProfile:
        """Re-read, change, save (with a backup). GameWatcher picks the file change up within a
        second; the dictionary of the active game is applied at once (TZ 6: a fixed stress
        works in the next line)."""
        stored = self.store.update(path, change)
        status = self.status()
        if self._parts is not None and status.profile_path == str(path):
            self._parts.speech.preparer.dictionary = pronunciation(stored.profile)
        return stored

    def add_game(self, exe_path: str) -> StoredProfile:
        """«Добавить игру по exe» (TZ 4.16): the title from the file's ProductName, else its
        name; install_dir is the exe's folder; the icon comes from the exe."""
        exe = Path(exe_path)
        install_dir = str(exe.parent)
        title = runtime.product_name(exe) or exe.stem
        icon = library.cache_icon(self.paths, title, install_dir, exe, library.default_icon_saver())
        self._unhide(install_dir)
        return pc.new_profile(self.store, exe.name, title, install_dir, icon=icon)

    def scan_library(self) -> library.LibraryScan:
        """Installed Steam / Epic / GOG games -> profiles (at start and by «Обновить»)."""
        with self._library_lock:
            result = library.scan_library(self.store, self.paths, self.settings)
        self.bus.publish(LibraryScanned(tuple(s.profile.title for s in result.added)))
        return result

    def icon_path(self, profile: Profile) -> Path | None:
        if not profile.icon:
            return None
        path = self.paths.root / profile.icon
        return path if path.is_file() else None

    def _unhide(self, install_dir: str) -> None:
        key = folder_key(install_dir)
        hidden = [d for d in self.settings.library_hidden if folder_key(d) != key]
        if len(hidden) != len(self.settings.library_hidden):
            self.apply_settings(self.settings.model_copy(update={"library_hidden": hidden}))

    def create_profile(self, exe: str, title: str | None = None) -> StoredProfile:
        return pc.new_profile(self.store, exe, title)

    def create_profile_for(self, window: WindowRef) -> StoredProfile:
        """«Создать профиль» from the notification: GameWatcher activates it within a second."""
        exe_path = runtime.exe_path(window.pid)
        install_dir = str(Path(exe_path).parent) if exe_path else None
        title = Path(window.exe).stem if not window.title else window.title[:60]
        return pc.new_profile(self.store, window.exe, title, install_dir)

    def never_ask(self, exe: str) -> None:
        """«Не спрашивать» (TZ 4.11): the exe goes to ``never_ask_exes``."""
        if exe.lower() not in {e.lower() for e in self.settings.never_ask_exes}:
            exes = [*self.settings.never_ask_exes, exe]
            self.apply_settings(self.settings.model_copy(update={"never_ask_exes": exes}))

    def delete_profile(self, path: Path) -> None:
        """The profile only, never the game; the file goes to backup. Its install folder is
        remembered so the library scan does not bring it back (TZ 1.18)."""
        install_dir = None
        try:
            install_dir = self.store.load(path).install_dir
        except (OSError, ValueError):
            log.debug("Удаляемый профиль %s не прочитан", path.name)
        self.store.backup_dir.mkdir(parents=True, exist_ok=True)
        path.replace(self.store.backup_dir / f"{path.stem}.deleted.json")
        if install_dir:
            key = folder_key(install_dir)
            if all(folder_key(d) != key for d in self.settings.library_hidden):
                hidden = [*self.settings.library_hidden, install_dir]
                self.apply_settings(self.settings.model_copy(update={"library_hidden": hidden}))

    def export_profile(self, path: Path, target: Path) -> None:
        self.store.export(path, target)

    def import_profile(self, source: Path) -> StoredProfile:
        return self.store.import_file(source)

    def import_characters_text(self, path: Path, text: str) -> pc.CharactersImport:
        return pc.import_characters_text(self.store, str(path), text)

    def merge_characters(self, path: Path, source: str, target: str) -> StoredProfile:
        return pc.merge_characters_in(self.store, str(path), source, target)

    def recent(self, count: int | None = None) -> list[HistoryEntry]:
        return self.history.recent(count)

    # ------------------------------------------------------------------ status

    def status(self) -> GameStatus:
        with self._lock:
            game, missing, title = self._game, self._missing, self._title
        if game is None:
            return GameStatus(self.settings.enabled)
        return GameStatus(
            self.settings.enabled,
            exe=game.window.exe,
            title=title or game.window.title,
            profile_path=game.profile_path,
            aspect=game.aspect,
            window=game.window,
            has_zones=not missing,
        )

    def _on_activated(self, event: Event) -> None:
        assert isinstance(event, GameActivated)
        title = None
        try:
            title = self.store.load(Path(event.profile_path)).title or None
        except (OSError, ValueError):
            log.debug("Профиль %s не прочитан для статуса", event.profile_path)
        with self._lock:
            self._game, self._missing, self._title = event, False, title
        self._set_zone_keys(self._hotkey_zones(Path(event.profile_path), event.aspect))

    def _hotkey_zones(self, path: Path, aspect: str) -> dict[str, str]:
        """Hotkeys of the active layout: registered only while the profile is active (4.12)."""
        try:
            layout = self.store.load(path).layout
        except (OSError, ValueError):
            return {}
        return {
            zone.hotkey: zone.id
            for zone in layout.zones
            if zone.kind == "text" and zone.trigger == "hotkey" and zone.hotkey
        }

    def _set_zone_keys(self, keys: dict[str, str]) -> None:
        if keys == self._zone_keys:
            return
        self._zone_keys = keys
        if keys:
            log.info("Клавиши зон: %s", ", ".join(f"{k} → {v}" for k, v in keys.items()))
        self._register_hotkeys()

    def _on_missing(self, _event: Event) -> None:
        with self._lock:
            self._missing = True

    def _on_closed(self, _event: Event) -> None:
        with self._lock:
            self._game, self._missing, self._title = None, False, None
        self._set_zone_keys({})


def tuning(settings: Settings) -> SpeechTuning:
    return SpeechTuning(
        base_rate=settings.base_rate,
        overlap=OverlapMode(settings.overlap_mode),
        hurry_rate=settings.hurry_rate,
        max_lag_s=settings.max_lag_s,
        low_priority_ttl_s=settings.low_priority_ttl_s,
        volume=settings.voice_volume,
        duck_level=settings.duck_level,
        duck_fade_s=settings.duck_fade_ms / 1000,
        duck_release_s=settings.duck_release_ms / 1000,
        cache_size=settings.tts_cache_size,
        output_device=settings.output_device,
    )
