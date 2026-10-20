"""Pausing the game for the zone editor (TZ 1.30) on a real child process of the test."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="NtSuspendProcess — Windows")


@pytest.fixture
def game() -> Iterator[psutil.Process]:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    process = psutil.Process(child.pid)
    yield process
    with contextlib.suppress(psutil.Error):
        process.resume()
    child.kill()
    child.wait()


def suspended(process: psutil.Process) -> bool:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if process.status() == psutil.STATUS_STOPPED:
            return True
        time.sleep(0.05)
    return False


def test_freeze_and_thaw(tmp_path: Path, game: psutil.Process) -> None:
    from subvoicer.engines.system.freeze import ProcessFreezer

    state = tmp_path / "frozen_games.json"
    freezer = ProcessFreezer(state)
    assert freezer.freeze(game.pid)
    assert suspended(game)
    assert str(game.pid) in json.loads(state.read_text(encoding="utf-8"))
    freezer.thaw(game.pid)
    assert game.status() != psutil.STATUS_STOPPED
    assert json.loads(state.read_text(encoding="utf-8")) == {}


def test_a_crashed_run_leaves_the_game_paused_and_the_next_start_resumes_it(
    tmp_path: Path, game: psutil.Process
) -> None:
    from subvoicer.engines.system.freeze import ProcessFreezer

    state = tmp_path / "frozen_games.json"
    assert ProcessFreezer(state).freeze(game.pid)  # ...and the app crashes
    assert suspended(game)
    ProcessFreezer(state).thaw_all()  # the next start
    assert game.status() != psutil.STATUS_STOPPED


def test_a_reused_pid_is_left_alone(tmp_path: Path, game: psutil.Process) -> None:
    from subvoicer.engines.system.freeze import ProcessFreezer

    state = tmp_path / "frozen_games.json"
    entry = {"exe": game.name(), "created": game.create_time() - 100}  # another process
    state.write_text(json.dumps({str(game.pid): entry}), encoding="utf-8")
    game.suspend()  # something else paused it, not us
    ProcessFreezer(state).thaw_all()
    assert suspended(game)
