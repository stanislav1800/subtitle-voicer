"""One running copy (TZ 4.14): the named Windows mutex ``SubtitleVoicer.SingleInstance``."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

MUTEX_NAME = "SubtitleVoicer.SingleInstance"
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        """True for the first copy; False if another one already holds the mutex."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, self.name)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if not handle:
            log.warning("Мьютекс %s не создан (%s)", self.name, ctypes.get_last_error())
            return True  # better to run twice than not at all
        if already:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
