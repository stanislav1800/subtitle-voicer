"""Manually driven clock for tests: time moves only when the test says so."""

from __future__ import annotations


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("FakeClock cannot go backwards")
        self._now += seconds

    def advance_ms(self, milliseconds: float) -> None:
        self.advance(milliseconds / 1000)
