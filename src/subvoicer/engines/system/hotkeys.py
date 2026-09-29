"""Global hotkeys via pynput (HotkeyService). Format: ``<ctrl>+<alt>+p``."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

log = logging.getLogger(__name__)


class PynputHotkeys:
    def __init__(self) -> None:
        self._listener: Any = None

    def start(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        from pynput import keyboard

        self.stop()
        safe = {combo: self._guard(combo, action) for combo, action in bindings.items()}
        self._listener = keyboard.GlobalHotKeys(safe)
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
