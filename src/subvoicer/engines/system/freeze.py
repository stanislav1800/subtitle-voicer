"""The game paused while the zone editor is open over it (TZ 4.13, 1.30): its process is
suspended (NtSuspendProcess through psutil) — no injection, every game, even where it has
no pause. A game left suspended would hang for good, so before suspending the process is
written to ``frozen_games.json`` (pid, exe, start time); ``thaw_all`` on the next start
resumes what a crashed run left — only the same process (a reused pid is left alone).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

import psutil

log = logging.getLogger(__name__)


class ProcessFreezer:
    id = "psutil"

    def __init__(self, state_file: Path) -> None:
        self._state_file = state_file
        self._lock = threading.Lock()
        self._frozen: dict[int, dict[str, Any]] = self._read_state()

    def freeze(self, pid: int) -> bool:
        """Suspend the game; False if Windows refused (an anti-cheat, another user's game)."""
        with self._lock:
            if pid in self._frozen:
                return True
            try:
                process = psutil.Process(pid)
                entry = {"exe": process.name(), "created": process.create_time()}
            except (psutil.Error, OSError) as exc:
                log.info("Пауза игры: процесс %s недоступен (%s)", pid, exc)
                return False
            self._frozen[pid] = entry
            if not self._write_state():
                del self._frozen[pid]  # never suspend what could not be saved
                return False
            try:
                process.suspend()
            except (psutil.Error, OSError) as exc:
                log.info("Пауза игры: Windows не дала остановить %s (%s)", entry["exe"], exc)
                del self._frozen[pid]
                self._write_state()
                return False
            log.info("Игра %s на паузе (процесс %s)", entry["exe"], pid)
            return True

    def thaw(self, pid: int) -> None:
        with self._lock:
            entry = self._frozen.pop(pid, None)
            if entry is None:
                return
            self._resume(pid, entry)
            self._write_state()

    def thaw_all(self) -> None:
        """Resume every game this app suspended (the editor closed, the app exits, or the
        previous run crashed with the editor open)."""
        with self._lock:
            pending, self._frozen = self._frozen, {}
            for pid, entry in pending.items():
                self._resume(pid, entry)
            if pending:
                self._write_state()

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _resume(pid: int, entry: dict[str, Any]) -> None:
        try:
            process = psutil.Process(pid)
            same = (
                process.name() == entry.get("exe")
                and abs(process.create_time() - float(entry.get("created", 0))) < 1.0
            )
            if not same:
                return  # the game is gone, the pid belongs to another process now
            process.resume()
            log.info("Игра %s снова идёт (процесс %s)", entry.get("exe"), pid)
        except psutil.NoSuchProcess:
            return
        except (psutil.Error, OSError):
            log.exception("Пауза игры: не удалось продолжить процесс %s", pid)

    def _read_state(self) -> dict[int, dict[str, Any]]:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.exception("Пауза игры: файл состояния %s повреждён", self._state_file)
            return {}
        if not isinstance(data, dict):
            return {}
        frozen = {}
        for pid, entry in data.items():
            if str(pid).isdigit() and isinstance(entry, dict):
                frozen[int(pid)] = entry
        return frozen

    def _write_state(self) -> bool:
        """Atomic, like the ducking state: a crash mid-write must not lose a frozen game."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            data = {str(pid): entry for pid, entry in self._frozen.items()}
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._state_file)
            return True
        except OSError:
            log.exception("Пауза игры: не удалось записать %s", self._state_file)
            return False
