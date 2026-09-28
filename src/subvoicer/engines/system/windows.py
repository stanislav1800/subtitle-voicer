"""Windows, processes, monitors, DPI (pywin32 + psutil)."""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

import psutil
import win32api
import win32con
import win32gui
import win32process

from subvoicer.core.models import WindowRef

log = logging.getLogger(__name__)

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
MIN_CLIENT_SIDE = 64

ScreenRect = tuple[int, int, int, int]  # left, top, right, bottom


def enable_dpi_awareness() -> bool:
    """Per-Monitor DPI Awareness v2 for the process (TZ 4.1): all coordinates are physical.

    Must run before any window is created or measured. Returns False if Windows refused
    (e.g. it was already set by a manifest), which is logged and otherwise harmless.
    """
    context = ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    ok = bool(ctypes.windll.user32.SetProcessDpiAwarenessContext(context))
    if not ok:
        log.debug("SetProcessDpiAwarenessContext не применён (код %s)", ctypes.GetLastError())
    return ok


def list_windows() -> list[WindowRef]:
    """Visible top-level windows with a title and a usable client area."""
    found: list[WindowRef] = []

    def visit(hwnd: int, _: object) -> bool:
        ref = describe(hwnd)
        if ref is not None:
            found.append(ref)
        return True

    win32gui.EnumWindows(visit, None)
    return found


def describe(hwnd: int) -> WindowRef | None:
    if not win32gui.IsWindowVisible(hwnd):
        return None
    title = win32gui.GetWindowText(hwnd)
    if not title:
        return None
    _, _, width, height = win32gui.GetClientRect(hwnd)
    if width < MIN_CLIENT_SIDE or height < MIN_CLIENT_SIDE:
        return None
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        exe = psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return None
    return WindowRef(hwnd, pid, exe, title, width, height)


def find_windows(exe: str, title: str | None = None) -> list[WindowRef]:
    """Windows of processes named ``exe`` (case-insensitive), optionally with ``title`` inside."""
    exe = exe.lower()
    needle = title.lower() if title else None
    return [
        w
        for w in list_windows()
        if w.exe.lower() == exe and (needle is None or needle in w.title.lower())
    ]


def is_window(hwnd: int) -> bool:
    return bool(win32gui.IsWindow(hwnd))


def client_rect_on_screen(hwnd: int) -> ScreenRect:
    _, _, width, height = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return left, top, left + width, top + height


@dataclass(frozen=True, slots=True)
class MonitorRef:
    index: int  # 1-based, in EnumDisplayMonitors order (as DXGI duplication counts them)
    rect: ScreenRect
    device: str


def monitors() -> list[MonitorRef]:
    result = []
    for i, (handle, _, _) in enumerate(win32api.EnumDisplayMonitors(), start=1):
        info = win32api.GetMonitorInfo(handle)
        result.append(MonitorRef(i, tuple(info["Monitor"]), info["Device"]))
    return result


def monitor_of_window(hwnd: int) -> MonitorRef:
    handle = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    device = win32api.GetMonitorInfo(handle)["Device"]
    for monitor in monitors():
        if monitor.device == device:
            return monitor
    raise LookupError(f"Монитор {device} окна {hwnd} не найден")
