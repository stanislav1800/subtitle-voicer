"""When to offer creating a profile for the foreground window (TZ 4.11)."""

from __future__ import annotations

from collections.abc import Iterable

FULLSCREEN_TOLERANCE_PX = 2

PixelRect = tuple[int, int, int, int]  # left, top, right, bottom in screen pixels


def covers_monitor(window: PixelRect, monitor: PixelRect) -> bool:
    """Fullscreen or borderless: the window rectangle is the monitor's within ±2 px."""
    return all(abs(w - m) <= FULLSCREEN_TOLERANCE_PX for w, m in zip(window, monitor, strict=True))


def _lower(names: Iterable[str]) -> set[str]:
    return {n.lower() for n in names}


class OfferPolicy:
    """Offer «создать профиль?» only for a window covering its monitor, an exe not excluded,
    not marked «не спрашивать» and not asked about yet in this run."""

    def __init__(self, excluded: Iterable[str], never_ask: Iterable[str]) -> None:
        self._asked: set[str] = set()
        self.update(excluded, never_ask)

    def update(self, excluded: Iterable[str], never_ask: Iterable[str]) -> None:
        """New lists from the settings; who was asked in this run is kept."""
        self._excluded = _lower(excluded)
        self._never_ask = _lower(never_ask)

    def should_offer(self, exe: str, fullscreen: bool) -> bool:
        key = exe.lower()
        if not fullscreen or key in self._excluded or key in self._never_ask:
            return False
        if key in self._asked:
            return False
        self._asked.add(key)
        return True
