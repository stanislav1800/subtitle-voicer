"""Per-zone stabilisation state machine over text masks (TZ 4.4): decides when to run OCR.

Driven by frame times, not wall-clock calls, so it is deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from subvoicer.pipeline.vision.change import (
    DEFAULT_CHANGE_RATIO,
    DEFAULT_EMPTY_RATIO,
    Mask,
    mask_changed,
    mask_empty,
)

_EPSILON_MS = 1e-6  # (1.2 - 1.0) * 1000 == 199.99999999999997 in floating point


class ZoneState(StrEnum):
    EMPTY = "EMPTY"
    CHANGING = "CHANGING"
    SHOWN = "SHOWN"


@dataclass(frozen=True, slots=True)
class StabilizationSettings:
    stable_ms: int = 200
    max_wait_ms: int = 1500
    change_ratio: float = DEFAULT_CHANGE_RATIO
    empty_ratio: float = DEFAULT_EMPTY_RATIO


@dataclass(frozen=True, slots=True)
class RunOcr:
    """Time to recognise the zone; ``t_change`` is when this text first started to change."""

    t_change: float


@dataclass(frozen=True, slots=True)
class Emptied:
    """The zone became empty."""


Decision = RunOcr | Emptied | None


class ZoneStabilizer:
    """EMPTY -> CHANGING -> (OCR once) -> SHOWN; any -> EMPTY when the mask empties.

    In CHANGING a change is measured against the previous frame's mask (is the text still
    settling?); in SHOWN against the mask that was recognised, so a slow fade that changes
    little per frame still counts once it adds up.
    """

    def __init__(self, settings: StabilizationSettings | None = None) -> None:
        self.settings = settings or StabilizationSettings()
        self.state = ZoneState.EMPTY
        self.was_empty = True  # the zone starts empty
        self._previous: Mask | None = None
        self._shown: Mask | None = None
        self._t_change = 0.0
        self._t_last_change = 0.0

    def update(self, mask: Mask, now: float) -> Decision:
        s = self.settings
        empty = mask_empty(mask, s.empty_ratio)
        previous, self._previous = self._previous, mask

        if empty:
            if self.state is ZoneState.EMPTY:
                return None
            self.state = ZoneState.EMPTY
            self.was_empty = True
            self._shown = None
            return Emptied()

        if self.state is ZoneState.EMPTY:
            self._begin_change(now)
            return self._maybe_ocr(mask, now)

        if self.state is ZoneState.SHOWN:
            assert self._shown is not None
            if mask_changed(self._shown, mask, s.change_ratio):
                self._begin_change(now)
                return self._maybe_ocr(mask, now)
            return None

        # CHANGING
        if previous is None or mask_changed(previous, mask, s.change_ratio):
            self._t_last_change = now
        return self._maybe_ocr(mask, now)

    def consume_was_empty(self) -> None:
        """The result processing used the flag (a new line after an empty zone)."""
        self.was_empty = False

    def _begin_change(self, now: float) -> None:
        self.state = ZoneState.CHANGING
        self._t_change = now
        self._t_last_change = now

    def _maybe_ocr(self, mask: Mask, now: float) -> Decision:
        s = self.settings
        stable = (now - self._t_last_change) * 1000 + _EPSILON_MS >= s.stable_ms
        waited_too_long = (now - self._t_change) * 1000 + _EPSILON_MS >= s.max_wait_ms
        if stable or waited_too_long:
            self.state = ZoneState.SHOWN
            self._shown = mask
            return RunOcr(self._t_change)
        return None
