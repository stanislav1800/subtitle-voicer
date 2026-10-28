"""Global hotkeys registered in Windows (RegisterHotKey, TZ 4.12, 1.40). Format:
``<ctrl>+<alt>+p``.

Keys are Windows virtual keys, not typed characters: the same in any keyboard layout (a
Russian layout made Ctrl+Alt+Z a «я» for a keyboard hook). The action runs once per press,
not on key repeat.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping

log = logging.getLogger(__name__)

MODIFIER_NAMES = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "cmd": "cmd"}
NAMED_VK = {
    "space": 0x20,
    "tab": 0x09,
    "home": 0x24,
    "end": 0x23,
    "insert": 0x2D,
    "delete": 0x2E,
    "page_up": 0x21,
    "page_down": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}

Combo = tuple[frozenset[str], int]


def parse(hotkey: str) -> Combo:
    """``<ctrl>+<alt>+z`` -> ({"ctrl", "alt"}, 0x5A)."""
    modifiers: set[str] = set()
    vk: int | None = None
    for part in hotkey.lower().split("+"):
        name = part.strip().strip("<>")
        if name in MODIFIER_NAMES:
            modifiers.add(MODIFIER_NAMES[name])
        elif len(name) == 1 and name.isascii() and name.isalnum():
            vk = ord(name.upper())
        elif name.startswith("f") and name[1:].isdigit():
            vk = 0x70 + int(name[1:]) - 1
        elif name in NAMED_VK:
            vk = NAMED_VK[name]
        else:
            raise ValueError(f"Неизвестная клавиша {part!r} в {hotkey!r}")
    if vk is None:
        raise ValueError(f"В сочетании {hotkey!r} нет основной клавиши")
    return frozenset(modifiers), vk


def guard(combo: str, action: Callable[[], None]) -> Callable[[], None]:
    """The action, its errors logged: a hotkey never takes the app down."""

    def run() -> None:
        try:
            action()
        except Exception:
            log.exception("Ошибка обработчика горячей клавиши %s", combo)

    return run


# ---------------------------------------------------------------- RegisterHotKey

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODIFIER_FLAGS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "shift": MOD_SHIFT, "cmd": MOD_WIN}
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def flags(modifiers: frozenset[str]) -> int:
    """RegisterHotKey modifiers of a combo, without key repeat."""
    result = MOD_NOREPEAT
    for name in modifiers:
        result |= MODIFIER_FLAGS[name]
    return result


class SystemHotkeys:
    """Global hotkeys registered in Windows (RegisterHotKey). A keyboard hook (pynput, until
    TZ 1.40) got nothing while a game run as administrator was in front — RDR2 from Rockstar
    Launcher (2026-10-28: 0 of 3 presses in the game, 3 of 3 here); a registered combo does.
    A combo another program has registered is refused: it is logged and left out (``taken``)."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self.taken: list[str] = []

    def start(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        self.stop()
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, args=(dict(bindings), ready), name="Hotkeys", daemon=True
        )
        self._thread.start()
        ready.wait(2.0)  # registered (or refused) before start returns

    def stop(self) -> None:
        import ctypes

        thread, self._thread = self._thread, None
        if thread is None:
            return
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(2.0)

    def _loop(self, bindings: dict[str, Callable[[], None]], ready: threading.Event) -> None:
        """The hotkeys belong to this thread: registered here, their messages come here."""
        import ctypes
        import ctypes.wintypes as wt

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # make the message queue
        actions: dict[int, tuple[str, Callable[[], None]]] = {}
        self.taken = []
        for number, (hotkey, action) in enumerate(bindings.items(), start=1):
            try:
                modifiers, vk = parse(hotkey)
            except ValueError:
                log.exception("Горячая клавиша %s пропущена", hotkey)
                continue
            if user32.RegisterHotKey(None, number, flags(modifiers), vk):
                actions[number] = (hotkey, guard(hotkey, action))
            else:
                self.taken.append(hotkey)
                log.warning("Горячая клавиша %s занята другой программой", hotkey)
        log.info("Горячие клавиши: %s", ", ".join(h for h, _ in actions.values()))
        ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in actions:
                    hotkey, run = actions[msg.wParam]
                    log.info("Горячая клавиша %s", hotkey)
                    threading.Thread(target=run, name="Hotkey", daemon=True).start()
        finally:
            for number in actions:
                user32.UnregisterHotKey(None, number)
