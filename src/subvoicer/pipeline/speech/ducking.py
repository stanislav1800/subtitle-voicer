"""Game volume envelope while lines are voiced (TZ 4.10): pure timing, no audio calls."""

from __future__ import annotations


class DuckEnvelope:
    """Gain of the game's sound: down to ``level`` over ``fade_s`` while a ducking line is
    on, back to 1.0 (over ``fade_s`` too) once ``release_s`` passed with no such line.

    Driven by ``update(wanted, now)`` every tick; a short gap between two lines (shorter
    than ``release_s``) keeps the game ducked.
    """

    def __init__(self, level: float = 0.4, fade_s: float = 0.15, release_s: float = 0.3) -> None:
        if not 0.0 <= level <= 1.0:
            raise ValueError(f"duck level must be in 0..1, got {level}")
        self.level = level
        self.fade_s = fade_s
        self.release_s = release_s
        self._from = 1.0
        self._to = 1.0
        self._t0 = 0.0
        self._release_at: float | None = None

    def update(self, wanted: bool, now: float) -> float:
        """Tell whether a ducking line is on now; return the gain to apply."""
        if wanted:
            self._release_at = None
            if self._to != self.level:
                self._retarget(self.level, now)
        elif self._to != 1.0:
            if self._release_at is None:
                self._release_at = now + self.release_s
            if now >= self._release_at:
                self._retarget(1.0, self._release_at)
                self._release_at = None
        return self.gain(now)

    def gain(self, now: float) -> float:
        duration = self._duration(self._from, self._to)
        if duration <= 0 or now >= self._t0 + duration:
            return self._to
        progress = max(0.0, now - self._t0) / duration
        return self._from + (self._to - self._from) * progress

    def idle(self, now: float) -> bool:
        """The game is back at full volume and nothing is pending."""
        return self._release_at is None and self.gain(now) == 1.0

    def _retarget(self, target: float, now: float) -> None:
        self._from = self.gain(now)
        self._to = target
        self._t0 = now

    def _duration(self, start: float, end: float) -> float:
        """The same speed for any distance: a partial fade takes proportionally less."""
        span = 1.0 - self.level
        if span <= 0:
            return 0.0
        return self.fade_s * abs(end - start) / span
