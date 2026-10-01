"""AppFacade: what the GUI may do (TZ 4.13). The UI talks only to this, config and core.

Owns the ``run`` parts (speech, GameWatcher, game sessions), the history of recent lines and
the current settings; every call is safe from the Qt thread.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from subvoicer.app import profile_commands as pc
from subvoicer.app import runtime, zone_tools
from subvoicer.app.bus import Bus
from subvoicer.app.game_runner import RunParts, build_run, pronunciation
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile import Profile, Zone
from subvoicer.config.profile_store import ProfileStore, StoredProfile
from subvoicer.config.resources import gender_exceptions, voice_catalog
from subvoicer.config.settings import Settings, load_settings, save_settings
from subvoicer.core.events import (
    Event,
    GameActivated,
    GameClosed,
    LayoutMissing,
    SettingsApplied,
)
from subvoicer.core.models import HistoryEntry, Priority, SpeechRequest, Voice, WindowRef
from subvoicer.pipeline.speaker.gender import GenderGuesser
from subvoicer.pipeline.speech.scheduler import OverlapMode
from subvoicer.services.history import History
from subvoicer.services.speech_service import SpeechTuning

log = logging.getLogger(__name__)

MANUAL_ID_START = 2_000_000  # ids of manual lines, apart from zone lines and repeats


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
        }
        self.bus.subscribe(GameActivated, self._on_activated)
        self.bus.subscribe(LayoutMissing, self._on_missing)
        self.bus.subscribe(GameClosed, self._on_closed)

    # ------------------------------------------------------------------ life cycle

    def start(self, hotkeys: bool = True) -> None:
        """Loads OCR and TTS (seconds) — call from a worker thread, not the Qt one."""
        parts = build_run(self.settings, self.bus, self.paths, hotkeys=False)
        parts.session.start()
        parts.speech.service.set_paused(not self.settings.enabled)
        self._parts = parts
        if hotkeys:
            self._hotkeys = runtime.make_hotkeys()
            self._register_hotkeys()

    def stop(self) -> None:
        hotkeys, self._hotkeys = self._hotkeys, None
        stop_hotkeys = getattr(hotkeys, "stop", None)
        if stop_hotkeys is not None:
            stop_hotkeys()
        parts, self._parts = self._parts, None
        if parts is not None:
            parts.session.stop()

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
        if new.hotkeys != old.hotkeys:
            self._register_hotkeys()
        self.bus.publish(SettingsApplied())

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

    def zone_hotkeys(self) -> dict[str, str]:
        """Hotkeys of hotkey zones in all profiles: combo -> «game · zone» (TZ 4.12)."""
        found: dict[str, str] = {}
        for stored in self.store.load_all():
            for layout in stored.profile.layouts.values():
                for zone in layout.zones:
                    if zone.trigger == "hotkey" and zone.hotkey:
                        title = stored.profile.title or stored.path.stem
                        found[zone.hotkey] = f"{title} · {zone.name or zone.id}"
        return found

    def output_devices(self) -> list[str]:
        return runtime.output_devices()

    def voices(self) -> list[Voice]:
        """Voices of the loaded engine; before it loads, the catalog (same ids for Silero)."""
        if self._parts is not None:
            return list(self._parts.speech.voices)
        return sorted(voice_catalog(pc.TTS_CATALOG).values(), key=lambda v: v.id)

    # ------------------------------------------------------------------ zone editor

    def grab_game_frame(self) -> zone_tools.Image | None:
        """A frozen frame of the active game for the editor «поверх игры»."""
        window = self.status().window
        return zone_tools.grab_window(window.hwnd) if window is not None else None

    def game_monitor(self) -> str | None:
        """Device name of the game's monitor (DISPLAY1 style; Qt names its screens the same)."""
        window = self.status().window
        return runtime.monitor_device(window.hwnd) if window is not None else None

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
        """«Добавить игру по exe»: exe name + its folder as install_dir (TZ 4.16 for stage 9)."""
        exe = Path(exe_path)
        return pc.new_profile(self.store, exe.name, exe.stem, str(exe.parent))

    def create_profile(self, exe: str, title: str | None = None) -> StoredProfile:
        return pc.new_profile(self.store, exe, title)

    def delete_profile(self, path: Path) -> None:
        """The profile only, never the game; the file goes to backup."""
        self.store.backup_dir.mkdir(parents=True, exist_ok=True)
        path.replace(self.store.backup_dir / f"{path.stem}.deleted.json")

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

    def _on_missing(self, _event: Event) -> None:
        with self._lock:
            self._missing = True

    def _on_closed(self, _event: Event) -> None:
        with self._lock:
            self._game, self._missing, self._title = None, False, None


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
