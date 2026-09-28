"""Fallback: DXGI Desktop Duplication of the window's monitor, cut to the window client area
(TZ 4.1) — for exclusive fullscreen games where WGC returns black frames."""

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

MONITOR_CHECK_S = 1.0


class DxgiFrameSource:
    id = "dxgi"
    display_name = "DXGI Desktop Duplication"
    capabilities = CaptureCapabilities(window_capture=False)

    def __init__(self, interval_ms: int = 50) -> None:
        self._interval_ms = interval_ms
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._running = threading.Event()
        self._failed = False
        self._thread: threading.Thread | None = None
        self._hwnd = 0

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "win32" and importlib.util.find_spec("windows_capture") is not None

    def start(self, hwnd: int) -> None:
        from windows_capture import DxgiDuplicationSession

        from subvoicer.engines.system import windows

        self._hwnd = hwnd
        monitor = windows.monitor_of_window(hwnd)
        session = DxgiDuplicationSession(monitor.index)  # raises if duplication is unavailable
        self._failed = False
        self._running.set()
        self._thread = threading.Thread(
            target=self._run, args=(session, monitor), name="DxgiCapture", daemon=True
        )
        self._thread.start()
        log.debug("DXGI: захват монитора %s для окна %s", monitor.index, hwnd)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def is_alive(self) -> bool:
        return self._running.is_set() and not self._failed

    def stop(self) -> None:
        self._running.clear()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _run(self, session: Any, monitor: Any) -> None:
        from subvoicer.engines.system import windows

        checked_at = time.monotonic()
        try:
            while self._running.is_set():
                if time.monotonic() - checked_at > MONITOR_CHECK_S:
                    checked_at = time.monotonic()
                    current = windows.monitor_of_window(self._hwnd)
                    if current.index != monitor.index:  # the window moved to another monitor
                        session.switch_monitor(current.index)
                        monitor = current
                frame = session.acquire_frame(timeout_ms=self._interval_ms)
                if frame is None:  # nothing changed on the monitor
                    continue
                image = self._cut_client_area(frame.to_bgr(), monitor.rect)
                if image is not None:
                    with self._lock:
                        self._latest = Frame(image, time.monotonic())
                time.sleep(self._interval_ms / 1000)
        except Exception:
            self._failed = True
            log.warning("DXGI: захват прерван", exc_info=True)

    def _cut_client_area(self, monitor_image: Any, monitor_rect: tuple[int, int, int, int]) -> Any:
        from subvoicer.engines.system import windows

        left, top, right, bottom = windows.client_rect_on_screen(self._hwnd)
        mx, my = monitor_rect[0], monitor_rect[1]
        height, width = monitor_image.shape[:2]
        x1, y1 = max(left - mx, 0), max(top - my, 0)
        x2, y2 = min(right - mx, width), min(bottom - my, height)
        if x2 <= x1 or y2 <= y1:
            return None
        return np.ascontiguousarray(monitor_image[y1:y2, x1:x2])
