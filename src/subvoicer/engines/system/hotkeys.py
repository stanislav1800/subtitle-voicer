"""Global hotkeys via pynput (HotkeyService). Format: ``<ctrl>+<alt>+p``.

Keys are matched by their Windows virtual key, not by the typed character: with a Russian
layout pynput reports Ctrl+Alt+Z as «я», and its GlobalHotKeys (which compares characters)
never fired. The action runs once per press, not on key repeat.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

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


class ComboTracker:
    """Pure part: modifiers held + the key pressed -> which combo fires (once per press)."""

    def __init__(self, combos: Mapping[Combo, str]) -> None:
        self._combos = dict(combos)
        self._held: set[str] = set()
        self._down: set[int] = set()

    def press(self, modifier: str | None, vk: int | None) -> str | None:
        if modifier is not None:
            self._held.add(modifier)
            return None
        if vk is None or vk in self._down:
            return None  # key repeat
        self._down.add(vk)
        return self._combos.get((frozenset(self._held), vk))

    def release(self, modifier: str | None, vk: int | None) -> None:
        if modifier is not None:
            self._held.discard(modifier)
        elif vk is not None:
            self._down.discard(vk)


def _modifier(key: Any) -> str | None:
    from pynput.keyboard import Key

    groups = {
        "ctrl": (Key.ctrl, Key.ctrl_l, Key.ctrl_r),
        "alt": (Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr),
        "shift": (Key.shift, Key.shift_l, Key.shift_r),
        "cmd": (Key.cmd, Key.cmd_l, Key.cmd_r),
    }
    for name, keys in groups.items():
        if key in keys:
            return name
    return None


def _vk(key: Any) -> int | None:
    vk = getattr(key, "vk", None)
    if vk is None:
        value = getattr(key, "value", None)
        vk = getattr(value, "vk", None)
    return int(vk) if vk is not None else None


class PynputHotkeys:
    def __init__(self) -> None:
        self._listener: Any = None
        self._lock = threading.Lock()

    def start(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        from pynput import keyboard

        self.stop()
        actions: dict[str, Callable[[], None]] = {}
        combos: dict[Combo, str] = {}
        for hotkey, action in bindings.items():
            try:
                combos[parse(hotkey)] = hotkey
            except ValueError:
                log.exception("Горячая клавиша %s пропущена", hotkey)
                continue
            actions[hotkey] = self._guard(hotkey, action)
        tracker = ComboTracker(combos)

        def on_press(key: Any) -> None:
            with self._lock:
                fired = tracker.press(_modifier(key), _vk(key))
            if fired is not None:
                log.info("Горячая клавиша %s", fired)
                # never block the keyboard hook: Windows silently drops a slow one
                threading.Thread(target=actions[fired], name="Hotkey", daemon=True).start()

        def on_release(key: Any) -> None:
            with self._lock:
                tracker.release(_modifier(key), _vk(key))

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()
        log.info("Горячие клавиши: %s", ", ".join(bindings))

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()

    @staticmethod
    def _guard(combo: str, action: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                action()
            except Exception:
                log.exception("Ошибка обработчика горячей клавиши %s", combo)

        return run
