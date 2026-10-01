"""GameWatcher (TZ 4.11): once a second looks at the foreground window and picks the profile.

The active game changes only when another game with a profile gets the focus (TZ 4.1):
a browser or a second monitor in focus does not stop voicing. The active game is rebuilt
when its window changes aspect ratio or its profile file changes on disk.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from subvoicer.config.profile_store import ProfileStore, StoredProfile
from subvoicer.core.events import (
    GameActivated,
    GameClosed,
    LayoutMissing,
    ProfileBound,
    ProfileOffered,
)
from subvoicer.core.models import WindowRef
from subvoicer.core.ports import Desktop, EventPublisher
from subvoicer.pipeline.games.aspect import aspect_key
from subvoicer.pipeline.games.matching import OfferPolicy, covers_monitor
from subvoicer.services.base import Service

log = logging.getLogger(__name__)

Signature = tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class ActiveGame:
    path: Path
    window: WindowRef
    aspect: str
    mtime: float


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class GameWatcher(Service):
    def __init__(
        self,
        store: ProfileStore,
        desktop: Desktop,
        offers: OfferPolicy,
        publisher: EventPublisher,
        interval_s: float = 1.0,
    ) -> None:
        super().__init__("GameWatcher", interval_s)
        self._store = store
        self._desktop = desktop
        self._offers = offers
        self._publisher = publisher
        self.active: ActiveGame | None = None
        self._seen: tuple[int, int, Signature] | None = None  # (hwnd, pid, profile files)
        self._own_pid = os.getpid()

    def step(self) -> None:
        self.check()

    def check(self) -> None:
        self._check_active()
        window = self._desktop.foreground()
        if window is None or window.pid == self._own_pid or not self._desktop.alive(window.pid):
            return  # our own windows (settings, zone editor) are never a game
        if self.active is not None and window.pid == self.active.window.pid:
            if window.hwnd != self.active.window.hwnd and not self._desktop.describe(
                self.active.window.hwnd
            ):
                log.info("Игра %s пересоздала окно", window.exe)  # e.g. fullscreen toggle
                self._activate_path(self.active.path, window)
            return
        signature = self._signature()
        seen = (window.hwnd, window.pid, signature)
        if seen == self._seen:
            return  # the same foreground window and the same profiles: decided already
        self._seen = seen
        self._consider(window)

    # ------------------------------------------------------------------ internals

    def _check_active(self) -> None:
        active = self.active
        if active is None:
            return
        if not self._desktop.alive(active.window.pid):
            log.info("Игра %s закрыта", active.window.exe)
            self.active = None
            self._seen = None
            self._publisher.publish(GameClosed(active.window.pid))
            return
        window = self._desktop.describe(active.window.hwnd) or active.window
        aspect = aspect_key(window.width, window.height)
        mtime = _mtime(active.path)
        if aspect != active.aspect or mtime != active.mtime:
            reason = (
                "сменилось соотношение сторон" if aspect != active.aspect else "профиль изменён"
            )
            log.info("Игра %s: %s — перечитываю профиль", window.exe, reason)
            self._activate_path(active.path, window)

    def _consider(self, window: WindowRef) -> None:
        profiles = self._store.load_all()
        stored = self._store.match(profiles, window.exe, self._desktop.exe_path(window.pid))
        fullscreen = self._fullscreen(window)
        if stored is None and fullscreen:
            library = self._store.bindable(profiles, self._desktop.exe_path(window.pid))
            if library is not None:
                stored = self._store.bind(library, window.exe)
                log.info("Игра %s привязана к профилю %s", window.exe, stored.path.name)
                self._publisher.publish(ProfileBound(str(stored.path), stored.profile.title))
        if stored is not None:
            self._activate(stored, window)
        elif self._offers.should_offer(window.exe, fullscreen):
            log.info("Окно %s без профиля — предлагаю создать", window.exe)
            self._publisher.publish(ProfileOffered(window))

    def _activate_path(self, path: Path, window: WindowRef) -> None:
        try:
            stored = StoredProfile(path, self._store.load(path))
        except (OSError, ValueError) as exc:
            log.error("Профиль %s не загружен: %s", path.name, exc)
            # keep the old session; retry when the file changes again
            if self.active is not None:
                self.active = ActiveGame(self.active.path, window, self.active.aspect, _mtime(path))
            return
        self._activate(stored, window)

    def _activate(self, stored: StoredProfile, window: WindowRef) -> None:
        aspect = aspect_key(window.width, window.height)
        self.active = ActiveGame(stored.path, window, aspect, _mtime(stored.path))
        log.info("Активный профиль: %s (%s, %s)", stored.path.name, window.exe, aspect)
        self._publisher.publish(GameActivated(str(stored.path), window, aspect))
        if aspect not in stored.profile.layouts:
            self._publisher.publish(LayoutMissing(str(stored.path), stored.profile.title, aspect))

    def _fullscreen(self, window: WindowRef) -> bool:
        try:
            client = self._desktop.client_rect(window.hwnd)
            monitor = self._desktop.monitor_rect(window.hwnd)
        except Exception:
            log.debug("Окно %s: не удалось проверить полноэкранность", window.hwnd, exc_info=True)
            return False
        return covers_monitor(client, monitor)

    def _signature(self) -> Signature:
        directory = self._store.directory
        if not directory.exists():
            return ()
        return tuple(sorted((p.name, _mtime(p)) for p in directory.glob("*.json")))
