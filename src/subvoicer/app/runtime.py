"""Process start-up and running pipelines (stage 2: the ``watch`` session)."""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from subvoicer.core.models import WindowRef

log = logging.getLogger(__name__)


class Startable(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()


def prepare_process() -> None:
    """Must run first: DPI awareness makes every window coordinate physical (TZ 4.1)."""
    if sys.platform == "win32":
        from subvoicer.engines.system import windows

        windows.enable_dpi_awareness()


def find_windows(exe: str, title: str | None = None) -> list[WindowRef]:
    from subvoicer.engines.system import windows

    return windows.find_windows(exe, title)


def all_windows() -> list[WindowRef]:
    from subvoicer.engines.system import windows

    return windows.list_windows()


class Session:
    """Services (or nested sessions) started together and stopped in reverse order, plus
    resources to release after them (audio stream, hotkeys)."""

    def __init__(
        self,
        services: Sequence[Startable],
        closers: Sequence[Callable[[], None]] = (),
        starters: Sequence[Callable[[], None]] = (),
    ) -> None:
        self._services = list(services)
        self._closers = list(closers)
        self._starters = list(starters)

    def start(self) -> None:
        for service in self._services:
            service.start()
        for start in self._starters:
            start()

    def stop(self) -> None:
        for service in reversed(self._services):
            service.stop()
        for close in self._closers:
            try:
                close()
            except Exception:
                log.exception("Ошибка при закрытии")


def dark_title_bar(hwnd: int) -> None:
    if sys.platform == "win32":
        from subvoicer.engines.system import windows

        windows.set_dark_title_bar(hwnd)


def acquire_single_instance() -> object | None:
    """The first copy gets a handle to keep (release with ``release_single_instance``);
    a second copy gets None (TZ 4.14)."""
    if sys.platform != "win32":
        return object()
    from subvoicer.engines.system.single_instance import SingleInstance

    instance = SingleInstance()
    return instance if instance.acquire() else None


def release_single_instance(handle: object | None) -> None:
    release = getattr(handle, "release", None)
    if release is not None:
        release()


def autostart_command() -> str:
    """``pythonw -m subvoicer`` of this environment: no console window at logon."""
    from pathlib import Path

    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")
    return f'"{windowed if windowed.exists() else exe}" -m subvoicer'


def set_autostart(enabled: bool) -> None:
    if sys.platform == "win32":
        from subvoicer.engines.system import autostart

        autostart.set_enabled(enabled, autostart_command())


def output_devices() -> list[str]:
    from subvoicer.engines.audio.output import output_devices as devices

    try:
        return devices()
    except Exception:
        log.exception("Список устройств вывода недоступен")
        return []


def make_hotkeys() -> object:
    from subvoicer.engines.system.hotkeys import PynputHotkeys

    return PynputHotkeys()
