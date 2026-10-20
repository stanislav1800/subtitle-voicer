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


def foreground() -> WindowRef | None:
    hwnd = win32gui.GetForegroundWindow()
    return describe(hwnd) if hwnd else None


def exe_path(pid: int) -> str | None:
    try:
        return str(psutil.Process(pid).exe())
    except (psutil.Error, OSError):
        return None


class WindowsDesktop:
    """The ``Desktop`` port over pywin32 + psutil."""

    def foreground(self) -> WindowRef | None:
        return foreground()

    def describe(self, hwnd: int) -> WindowRef | None:
        return describe(hwnd) if is_window(hwnd) else None

    def client_rect(self, hwnd: int) -> ScreenRect:
        return client_rect_on_screen(hwnd)

    def monitor_rect(self, hwnd: int) -> ScreenRect:
        return monitor_of_window(hwnd).rect

    def exe_path(self, pid: int) -> str | None:
        return exe_path(pid)

    def alive(self, pid: int) -> bool:
        return bool(psutil.pid_exists(pid))


DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def set_dark_title_bar(hwnd: int) -> None:
    """Dark standard title bar (docs/UI.md: the MVP keeps the Windows frame)."""
    value = ctypes.c_int(1)
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
        )
    except OSError:
        log.debug("Тёмный заголовок окна недоступен", exc_info=True)


HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002


def keep_topmost(hwnd: int) -> None:
    """Back on top of a borderless game without taking the focus (overlay, notifications)."""
    ctypes.windll.user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    )


HWND_NOTOPMOST = -2
GWLP_HWNDPARENT = -8  # for a top-level window: its owner


def own_by(hwnd: int, owner: int) -> None:
    """Make ``hwnd`` an owned window of ``owner`` (another process's window is allowed): it
    stays right above the owner, any window over the owner covers it too, it hides with it."""
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    if user32.GetWindowLongPtrW(hwnd, GWLP_HWNDPARENT) == owner:
        return
    user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, owner)
    user32.SetWindowPos(
        hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    )
    log.info("Оверлей привязан к окну игры %s", owner)


WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+; older ones refuse it


def exclude_from_capture(hwnd: int) -> None:
    """Our window is not in any capture (DXGI, WGC, screenshots, recording): the screen
    capture does not read our own notifications and overlay over the game (TZ 4.1)."""
    if not ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        log.debug("Окно %s не скрыто от захвата: %s", hwnd, ctypes.WinError())


def force_foreground(hwnd: int) -> None:
    """Take the focus from the game (the zone editor): Windows lets a process that just got
    a key event do it, so a released Alt is sent first."""
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    keep_topmost(hwnd)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        log.debug("SetForegroundWindow отказал для %s", hwnd, exc_info=True)
