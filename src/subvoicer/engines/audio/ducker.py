"""Ducking through pycaw (ISimpleAudioVolume) with the original volumes kept on disk (TZ 4.10).

Windows remembers per-app volume between runs, so a game left ducked would stay quiet
forever. The original volume of each exe is taken once, on the first change, and written to
``ducking_state.json`` before the volume is touched; ``restore_all`` puts it back and drops
the entry. An exe whose sessions are not found (the game is closed) keeps its entry: it is
restored the next time its sessions are seen, and a later duck starts from the saved value.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SESSIONS_TTL_S = 1.0  # enumerating sessions takes ~20 ms: not on every 10 ms tick


class PycawDucker:
    id = "pycaw"

    def __init__(self, state_file: Path) -> None:
        self._state_file = state_file
        self._lock = threading.Lock()
        self._original: dict[str, float] = self._read_state()
        self._local = threading.local()  # COM objects belong to the thread that made them
        self._exe_by_pid: dict[int, str] = {}
        self._failed: set[str] = set()  # exes already reported, so the log is not flooded

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "win32" and importlib.util.find_spec("pycaw") is not None

    # ------------------------------------------------------------------ Ducker

    def set_gain(self, pid: int, gain: float) -> None:
        exe = self._exe(pid)
        if exe is None:
            return
        sessions = self._sessions(exe)
        with self._lock:
            original = self._original.get(exe)
            if original is None:
                if gain >= 1.0 or not sessions:
                    return
                original = self._volume(sessions[0])
                if original is None:
                    return
                self._original[exe] = original
                if not self._write_state():
                    del self._original[exe]  # never duck what could not be saved
                    return
                log.info("Ducking: исходная громкость %s — %.0f%%", exe, original * 100)
        for session in sessions:
            self._set_volume(exe, session, original * gain)

    def restore_all(self) -> None:
        with self._lock:
            pending = dict(self._original)
        if not pending:
            return
        restored: list[str] = []
        for exe, volume in pending.items():
            sessions = self._sessions(exe, fresh=True)
            for session in sessions:
                self._set_volume(exe, session, volume)
            if sessions:
                restored.append(exe)
                log.info("Ducking: громкость %s восстановлена (%.0f%%)", exe, volume * 100)
            else:
                log.info("Ducking: %s не запущен, восстановлю громкость позже", exe)
        with self._lock:
            for exe in restored:
                self._original.pop(exe, None)
            self._write_state()

    # ------------------------------------------------------------------ sessions

    def _exe(self, pid: int) -> str | None:
        exe = self._exe_by_pid.get(pid)
        if exe is None:
            import psutil

            try:
                exe = psutil.Process(pid).name().lower()
            except psutil.Error:
                return None
            self._exe_by_pid[pid] = exe
        return exe

    def _sessions(self, exe: str, fresh: bool = False) -> list[Any]:
        cache: dict[str, tuple[float, list[Any]]] | None = getattr(self._local, "cache", None)
        if cache is None:
            import comtypes

            comtypes.CoInitialize()
            cache = self._local.cache = {}
        now = time.monotonic()
        cached = cache.get(exe)
        if cached is not None and not fresh and now - cached[0] < SESSIONS_TTL_S:
            return cached[1]
        sessions = self._find_sessions(exe)
        cache[exe] = (now, sessions)
        return sessions

    @staticmethod
    def _find_sessions(exe: str) -> list[Any]:
        from pycaw.pycaw import AudioUtilities

        found = []
        own = os.getpid()  # our own voice is never ducked, even if the game is python.exe
        try:
            for session in AudioUtilities.GetAllSessions():
                process = session.Process
                if process is None or process.pid == own:
                    continue
                if process.name().lower() == exe:
                    found.append(session.SimpleAudioVolume)
        except Exception:
            log.exception("Ducking: не удалось получить аудиосессии")
        return found

    def _volume(self, session: Any) -> float | None:
        try:
            return float(session.GetMasterVolume())
        except Exception:
            log.debug("Ducking: громкость сессии не читается", exc_info=True)
            return None

    def _set_volume(self, exe: str, session: Any, volume: float) -> None:
        """A closed or exclusive-mode session silently stays as it is (TZ 4.10)."""
        try:
            session.SetMasterVolume(min(1.0, max(0.0, volume)), None)
        except Exception as exc:
            if exe not in self._failed:
                self._failed.add(exe)
                log.info("Ducking: громкость %s не меняется (%s)", exe, exc)

    # ------------------------------------------------------------------ disk state

    def _read_state(self) -> dict[str, float]:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.exception("Ducking: файл состояния %s повреждён", self._state_file)
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(exe).lower(): float(volume)
            for exe, volume in data.items()
            if isinstance(volume, int | float) and 0.0 <= volume <= 1.0
        }

    def _write_state(self) -> bool:
        """Atomic: a crash mid-write must not lose the original volumes."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._original, indent=2), encoding="utf-8")
            os.replace(tmp, self._state_file)
        except OSError:
            log.exception("Ducking: не удалось записать %s", self._state_file)
            return False
        return True
