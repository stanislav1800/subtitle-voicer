"""Hotkeys registered in Windows (RegisterHotKey): a press reaches the action, a combo taken
by another registration is reported, stop gives it back."""

from __future__ import annotations

import ctypes
import sys
import threading

import pytest

from subvoicer.engines.system.hotkeys import SystemHotkeys

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
COMBO = "<ctrl>+<alt>+<shift>+<f11>"  # unlikely to be taken by anything else


def foreground_elevated() -> bool:
    """Windows drops input sent to a window run as administrator (a game in front)."""
    import win32api
    import win32con
    import win32gui
    import win32process
    import win32security

    _, pid = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
    except Exception:
        return True


def press(*vks: int) -> None:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def test_a_registered_combo_fires_and_is_released() -> None:
    if foreground_elevated():
        pytest.skip("на переднем плане окно администратора: Windows не пропустит нажатие теста")
    fired = threading.Event()
    keys = SystemHotkeys()
    keys.start({COMBO: fired.set})
    try:
        assert keys.taken == []
        press(0x11, 0x12, 0x10, 0x7A)  # ctrl, alt, shift, F11
        assert fired.wait(2.0)
        other = SystemHotkeys()
        other.start({COMBO: lambda: None})
        assert other.taken == [COMBO]
        other.stop()
    finally:
        keys.stop()
    again = SystemHotkeys()
    again.start({COMBO: lambda: None})
    assert again.taken == []
    again.stop()
