"""Window capture via Windows Graphics Capture (windows-capture)."""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
import time
from typing import Any

import numpy as np

from subvoicer.core.models import CaptureCapabilities, Frame

log = logging.getLogger(__name__)


class WgcFrameSource:
    """Keeps only the latest frame. WGC delivers a frame when the window content changes
    (plus one right after start), so a static window may go quiet for a long time."""

    id = "wgc"
    display_name = "Windows Graphics Capture"
    capabilities = CaptureCapabilities(window_capture=True)

    def __init__(self, min_interval_ms: int = 50) -> None:
        self._min_interval_ms = min_interval_ms
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._closed = False
        self._control: Any = None

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "win32" and importlib.util.find_spec("windows_capture") is not None

    def start(self, hwnd: int) -> None:
        from windows_capture import WindowsCapture

        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=hwnd,
            minimum_update_interval=self._min_interval_ms,
        )
        capture.frame_handler = self._on_frame
        capture.closed_handler = self._on_closed
        self._closed = False
        self._control = capture.start_free_threaded()
        log.debug("WGC: захват окна %s запущен", hwnd)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def is_alive(self) -> bool:
        if self._control is None or self._closed:
            return False
        return not self._control.is_finished()

    def stop(self) -> None:
        control, self._control = self._control, None
        if control is not None:
            try:
                control.stop()
            except Exception:
                log.debug("WGC: ошибка при остановке", exc_info=True)

    def _on_frame(self, frame: Any, _control: Any) -> None:
        # the buffer is a view of the native frame: copy before the callback returns
        image = np.array(frame.frame_buffer, dtype=np.uint8, copy=True)
        with self._lock:
            self._latest = Frame(image, time.monotonic())

    def _on_closed(self) -> None:
        self._closed = True
        log.debug("WGC: сессия захвата закрыта")
