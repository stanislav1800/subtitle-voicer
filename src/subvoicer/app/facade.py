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

from subvoicer.app.bus import Bus
from subvoicer.app.game_runner import RunParts, build_run
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile_store import ProfileStore
from subvoicer.config.settings import Settings, load_settings
from subvoicer.core.events import (
    Event,
    GameActivated,
    GameClosed,
    LayoutMissing,
)
from subvoicer.core.models import HistoryEntry, Priority, SpeechRequest, Voice, WindowRef
from subvoicer.services.history import History

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
        self.bus.subscribe(GameActivated, self._on_activated)
        self.bus.subscribe(LayoutMissing, self._on_missing)
        self.bus.subscribe(GameClosed, self._on_closed)

    # ------------------------------------------------------------------ life cycle

    def start(self, hotkeys: bool = True) -> None:
        """Loads OCR and TTS (seconds) — call from a worker thread, not the Qt one."""
        parts = build_run(self.settings, self.bus, self.paths, hotkeys=hotkeys)
        parts.session.start()
        parts.speech.service.set_paused(not self.settings.enabled)
        self._parts = parts

    def stop(self) -> None:
        parts, self._parts = self._parts, None
        if parts is not None:
            parts.session.stop()

    @property
    def ready(self) -> bool:
        return self._parts is not None

    def subscribe(self, event_type: type[Event], handler: Callable[[Event], None]) -> None:
        self.bus.subscribe(event_type, handler)

    # ------------------------------------------------------------------ speech

    def set_enabled(self, enabled: bool) -> None:
        self.settings = self.settings.model_copy(update={"enabled": enabled})
        if self._parts is not None:
            self._parts.speech.service.set_paused(not enabled)

    def repeat(self) -> None:
        if self._parts is not None:
            self._parts.speech.service.repeat()

    def skip(self) -> None:
        if self._parts is not None:
            self._parts.speech.service.skip()

    def say(self, text: str, voice_id: str, speaker: str | None = None) -> None:
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
        )
        self._parts.speech.service.submit(request)

    def voices(self) -> list[Voice]:
        return list(self._parts.speech.voices) if self._parts is not None else []

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
