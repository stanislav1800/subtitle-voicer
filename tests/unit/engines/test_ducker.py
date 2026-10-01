"""PycawDucker's bookkeeping on fake sessions: the disk state is what saves the game's volume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from subvoicer.engines.audio.ducker import PycawDucker


class FakeVolume:
    def __init__(self, volume: float, broken: bool = False) -> None:
        self.volume = volume
        self.broken = broken  # e.g. an exclusive-mode session

    def GetMasterVolume(self) -> float:
        return self.volume

    def SetMasterVolume(self, volume: float, _context: Any) -> None:
        if self.broken:
            raise OSError("AUDCLNT_E_EXCLUSIVE_MODE_ONLY")
        self.volume = volume


class World:
    """Running processes and their audio sessions."""

    def __init__(self) -> None:
        self.exes = {1: "game.exe"}
        self.sessions: dict[str, list[FakeVolume]] = {"game.exe": [FakeVolume(0.8)]}

    def ducker(self, state: Path) -> PycawDucker:
        ducker = PycawDucker(state)
        ducker._exe = self.exes.get  # type: ignore[method-assign]
        ducker._sessions = lambda exe, fresh=False: self.sessions.get(exe, [])  # type: ignore[method-assign]
        return ducker


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "SubtitleVoicer" / "ducking_state.json"


def test_original_is_saved_before_ducking_and_restored(state: Path) -> None:
    world = World()
    ducker = world.ducker(state)
    ducker.set_gain(1, 0.5)
    assert json.loads(state.read_text()) == {"game.exe": 0.8}
    assert world.sessions["game.exe"][0].volume == pytest.approx(0.4)
    ducker.set_gain(1, 0.25)  # relative to the original, not to the ducked value
    assert world.sessions["game.exe"][0].volume == pytest.approx(0.2)
    ducker.restore_all()
    assert world.sessions["game.exe"][0].volume == pytest.approx(0.8)
    assert json.loads(state.read_text()) == {}


def test_all_sessions_of_the_exe_follow(state: Path) -> None:
    world = World()
    world.sessions["game.exe"].append(FakeVolume(0.8))
    world.ducker(state).set_gain(1, 0.5)
    assert [s.volume for s in world.sessions["game.exe"]] == pytest.approx([0.4, 0.4])


def test_full_gain_without_ducking_touches_nothing(state: Path) -> None:
    world = World()
    world.ducker(state).set_gain(1, 1.0)
    assert not state.exists()
    assert world.sessions["game.exe"][0].volume == 0.8


def test_after_a_kill_the_next_start_restores(state: Path) -> None:
    world = World()
    world.ducker(state).set_gain(1, 0.4)  # the process dies here: no restore_all
    assert world.sessions["game.exe"][0].volume == pytest.approx(0.32)
    world.ducker(state).restore_all()  # the next start
    assert world.sessions["game.exe"][0].volume == pytest.approx(0.8)
    assert json.loads(state.read_text()) == {}


def test_closed_game_keeps_its_entry_and_ducks_from_the_saved_value(state: Path) -> None:
    world = World()
    ducker = world.ducker(state)
    ducker.set_gain(1, 0.4)
    world.sessions["game.exe"] = []  # the game closed while ducked
    ducker.restore_all()
    assert json.loads(state.read_text()) == {"game.exe": 0.8}
    world.sessions["game.exe"] = [FakeVolume(0.32)]  # Windows remembered the ducked volume
    ducker = world.ducker(state)
    ducker.set_gain(1, 0.5)
    assert world.sessions["game.exe"][0].volume == pytest.approx(0.4)
    ducker.restore_all()
    assert world.sessions["game.exe"][0].volume == pytest.approx(0.8)


def test_exclusive_session_does_not_crash(state: Path) -> None:
    world = World()
    world.sessions["game.exe"] = [FakeVolume(0.8, broken=True)]
    ducker = world.ducker(state)
    ducker.set_gain(1, 0.4)
    ducker.restore_all()
    assert world.sessions["game.exe"][0].volume == 0.8


def test_unknown_process_and_silent_game(state: Path) -> None:
    world = World()
    world.sessions["game.exe"] = []
    ducker = world.ducker(state)
    ducker.set_gain(2, 0.4)
    ducker.set_gain(1, 0.4)
    assert not state.exists()


def test_damaged_state_file_is_ignored(state: Path) -> None:
    state.parent.mkdir(parents=True)
    state.write_text("{oops", encoding="utf-8")
    World().ducker(state).restore_all()
    state.write_text('{"game.exe": 7, "other.exe": "x"}', encoding="utf-8")
    world = World()
    world.ducker(state).restore_all()
    assert world.sessions["game.exe"][0].volume == 0.8
