"""GameWatcher on a fake desktop: which game is active and when the profile switches (4.1, 4.11)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from subvoicer.config.profile import Profile
from subvoicer.config.profile_store import ProfileStore
from subvoicer.core.events import (
    GameActivated,
    GameClosed,
    LayoutMissing,
    ProfileBound,
    ProfileOffered,
)
from subvoicer.core.models import WindowRef
from subvoicer.core.ports import ScreenRect
from subvoicer.pipeline.games.matching import OfferPolicy
from subvoicer.services.game_watcher import GameWatcher
from tests.fakes.capture import RecordingBus
from tests.unit.config.profiles import profile_data

MONITOR = (0, 0, 1920, 1080)
GAME_A = WindowRef(10, 100, "GameA.exe", "Game A", 1920, 1080)
GAME_B = WindowRef(20, 200, "GameB.exe", "Game B", 1920, 1080)
BROWSER = WindowRef(30, 300, "firefox.exe", "Firefox", 1920, 1080)
EDITOR = WindowRef(40, 400, "Code.exe", "Code", 1200, 800)


class FakeDesktop:
    def __init__(self) -> None:
        self.windows = {w.hwnd: w for w in (GAME_A, GAME_B, BROWSER, EDITOR)}
        self.focus: WindowRef | None = None
        self.dead: set[int] = set()
        self.paths = {100: "C:\\Games\\A\\GameA.exe", 200: "D:\\Lib\\B\\bin\\GameB.exe"}

    def foreground(self) -> WindowRef | None:
        return self.focus

    def describe(self, hwnd: int) -> WindowRef | None:
        return self.windows.get(hwnd)

    def client_rect(self, hwnd: int) -> ScreenRect:
        w = self.windows[hwnd]
        return (0, 0, w.width, w.height)

    def monitor_rect(self, hwnd: int) -> ScreenRect:
        return MONITOR

    def exe_path(self, pid: int) -> str | None:
        return self.paths.get(pid)

    def alive(self, pid: int) -> bool:
        return pid not in self.dead


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.store = ProfileStore(tmp_path / "profiles")
        self.desktop = FakeDesktop()
        self.bus = RecordingBus()
        self.watcher = GameWatcher(
            self.store, self.desktop, OfferPolicy(["firefox.exe"], []), self.bus
        )

    def add(self, **fields: object) -> Path:
        return self.store.save(Profile.model_validate(profile_data(**fields))).path

    def focus(self, window: WindowRef) -> None:
        self.desktop.focus = window
        self.watcher.check()

    def activated(self) -> list[str]:
        return [e.window.exe for e in self.bus.of(GameActivated)]  # type: ignore[attr-defined]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_focus_switches_between_games_but_not_to_other_windows(rig: Rig) -> None:
    rig.add(exe="GameA.exe")
    rig.add(exe="GameB.exe")
    rig.focus(GAME_A)
    rig.focus(BROWSER)  # a browser or another monitor: the game keeps being voiced
    rig.focus(EDITOR)
    assert rig.activated() == ["GameA.exe"]
    rig.focus(GAME_B)
    rig.focus(GAME_A)
    assert rig.activated() == ["GameA.exe", "GameB.exe", "GameA.exe"]
    rig.watcher.check()  # nothing new
    assert len(rig.activated()) == 3


def test_closed_game(rig: Rig) -> None:
    rig.add(exe="GameA.exe")
    rig.focus(GAME_A)
    rig.desktop.dead.add(GAME_A.pid)
    rig.watcher.check()
    assert [e.pid for e in rig.bus.of(GameClosed)] == [100]  # type: ignore[attr-defined]
    assert rig.watcher.active is None


def test_aspect_change_and_profile_edit_rebuild(rig: Rig) -> None:
    path = rig.add(exe="GameA.exe")
    rig.focus(GAME_A)
    rig.desktop.windows[GAME_A.hwnd] = WindowRef(10, 100, "GameA.exe", "Game A", 1280, 1024)
    rig.watcher.check()
    events = rig.bus.of(GameActivated)
    assert [e.aspect for e in events] == ["16:9", "1280x1024"]  # type: ignore[attr-defined]
    assert rig.bus.of(LayoutMissing)  # no zones for 1280x1024
    data = json.loads(path.read_text(encoding="utf-8"))
    data["title"] = "Правка"
    path.write_text(json.dumps(data), encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))
    rig.watcher.check()
    assert len(rig.bus.of(GameActivated)) == 3


def test_recreated_window_is_followed(rig: Rig) -> None:
    rig.add(exe="GameA.exe")
    rig.focus(GAME_A)
    new_window = WindowRef(11, 100, "GameA.exe", "Game A", 1920, 1080)
    del rig.desktop.windows[GAME_A.hwnd]
    rig.desktop.windows[11] = new_window
    rig.focus(new_window)
    events = rig.bus.of(GameActivated)
    assert [e.window.hwnd for e in events] == [10, 11]  # type: ignore[attr-defined]


def test_offer_only_for_fullscreen_not_excluded_once(rig: Rig) -> None:
    rig.focus(EDITOR)  # not fullscreen
    rig.focus(BROWSER)  # excluded
    rig.focus(GAME_A)
    rig.focus(EDITOR)
    rig.focus(GAME_A)  # asked already in this run
    offers = [e.window.exe for e in rig.bus.of(ProfileOffered)]  # type: ignore[attr-defined]
    assert offers == ["GameA.exe"]


def test_new_profile_is_picked_up_while_the_game_has_focus(rig: Rig) -> None:
    rig.focus(GAME_A)
    assert rig.activated() == []
    rig.add(exe="GameA.exe")
    rig.watcher.check()
    assert rig.activated() == ["GameA.exe"]


def test_library_profile_is_bound_for_a_fullscreen_game(rig: Rig) -> None:
    path = rig.add(exe=None, install_dir="D:\\Lib\\B", title="Игра Б")
    rig.focus(GAME_B)
    assert [e.title for e in rig.bus.of(ProfileBound)] == ["Игра Б"]  # type: ignore[attr-defined]
    assert rig.activated() == ["GameB.exe"]
    assert not path.exists()
    assert rig.store.load_all()[0].profile.exe == "GameB.exe"
