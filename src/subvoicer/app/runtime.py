"""Process start-up and running pipelines (stage 2: the ``watch`` session)."""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Sequence

from subvoicer.core.models import WindowRef
from subvoicer.services.base import Service

log = logging.getLogger(__name__)


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
    """A set of services started and stopped together (in reverse order)."""

    def __init__(self, services: Sequence[Service]) -> None:
        self._services = list(services)

    def start(self) -> None:
        for service in self._services:
            service.start()

    def stop(self) -> None:
        for service in reversed(self._services):
            service.stop()
