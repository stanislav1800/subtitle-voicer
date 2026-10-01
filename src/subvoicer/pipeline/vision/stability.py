"""Per-zone stabilisation state machine over text masks (TZ 4.4): decides when to run OCR.

Driven by frame times, not wall-clock calls, so it is deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

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
    typing_max_wait_ms: int = 5000  # while the mask only grows (text being typed)
    growth_share: float = 0.8  # a change is growth when >= 80% of changed pixels were added
    min_growth_ratio: float = 0.0002  # growth of 0.02% of the zone (about one character) counts
    change_ratio: float = DEFAULT_CHANGE_RATIO
    empty_ratio: float = DEFAULT_EMPTY_RATIO
    flicker_ms: int = 300  # a shorter empty spell with the same text after it is a flicker


@dataclass(frozen=True, slots=True)
class RunOcr:
    """Time to recognise the zone; ``t_change`` is when this text first started to change.

    ``still_typing``: forced by ``typing_max_wait_ms`` while characters keep appearing, so
    the last word is probably cut.
    """

    t_change: float
    still_typing: bool = False


@dataclass(frozen=True, slots=True)
class Emptied:
    """The zone became empty."""


Decision = RunOcr | Emptied | None


class ZoneStabilizer:
    """EMPTY -> CHANGING -> (OCR once) -> SHOWN; any -> EMPTY when the mask empties.

    In CHANGING a change is measured against the previous frame's mask (is the text still
    settling?); in SHOWN against the mask that was recognised, so a slow fade that changes
    little per frame still counts once it adds up.

    Typing: while every change only adds pixels (characters appear, nothing disappears), the
    OCR waits for the typing to stop (``stable_ms``) up to ``typing_max_wait_ms`` instead of
    ``max_wait_ms``, so typed text is read once as a whole. A flickering background adds
    and removes pixels, so it still gets OCR after ``max_wait_ms``. One typed character is
    far below ``change_ratio`` of a subtitle zone, so in CHANGING growth of
    ``min_growth_ratio`` also counts as a change; edge jitter adds and removes alike and
    does not. In SHOWN only ``change_ratio`` counts (a live game scene often flashes small
    bright blobs behind a still subtitle).
    """

    def __init__(self, settings: StabilizationSettings | None = None) -> None:
        self.settings = settings or StabilizationSettings()
        self.state = ZoneState.EMPTY
        self.was_empty = True  # the zone starts empty
        self.empty_ms = float("inf")  # how long the last empty spell lasted (TZ 1.17)
        self._t_emptied: float | None = None
        self._previous: Mask | None = None
        self._shown: Mask | None = None
        self._t_change = 0.0
        self._t_last_change = 0.0
        self._growing = True

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
            self._t_emptied = now
            return Emptied()

        if self.state is ZoneState.EMPTY:
            if self._t_emptied is not None:
                self.empty_ms = (now - self._t_emptied) * 1000
            self._begin_change(now, growing=True)
            return self._maybe_ocr(mask, now)

        if self.state is ZoneState.SHOWN:
            assert self._shown is not None
            # only a real change (change_ratio) leaves SHOWN: small growth here is usually a
            # bright blob of the background behind unchanged text, not typing
            if mask_changed(self._shown, mask, s.change_ratio):
                self._begin_change(now, growing=self._is_growth(self._shown, mask))
                return self._maybe_ocr(mask, now)
            return None

        # CHANGING
        if previous is None:
            self._t_last_change = now
        elif mask_changed(previous, mask, s.change_ratio):
            self._t_last_change = now
            if not self._is_growth(previous, mask):
                self._growing = False
        elif self._grew(previous, mask):
            self._t_last_change = now
        return self._maybe_ocr(mask, now)

    @property
    def flickered(self) -> bool:
        """The zone was empty only for a moment (shorter than ``flicker_ms``)."""
        return self.was_empty and self.empty_ms < self.settings.flicker_ms

    def consume_was_empty(self) -> None:
        """The result processing used the flag (a new line after an empty zone)."""
        self.was_empty = False

    def _begin_change(self, now: float, *, growing: bool) -> None:
        self.state = ZoneState.CHANGING
        self._t_change = now
        self._t_last_change = now
        self._growing = growing

    def _is_growth(self, before: Mask, after: Mask) -> bool:
        if before.shape != after.shape:
            return False
        added, removed = _added_removed(before, after)
        changed = added + removed
        return changed == 0 or added >= self.settings.growth_share * changed

    def _grew(self, before: Mask, after: Mask) -> bool:
        """Small growth-shaped change: a character or two typed since ``before``."""
        if before.shape != after.shape:
            return False
        added, removed = _added_removed(before, after)
        enough = added >= self.settings.min_growth_ratio * after.size
        return enough and added >= self.settings.growth_share * (added + removed)

    def _maybe_ocr(self, mask: Mask, now: float) -> Decision:
        s = self.settings
        stable = (now - self._t_last_change) * 1000 + _EPSILON_MS >= s.stable_ms
        limit = s.typing_max_wait_ms if self._growing else s.max_wait_ms
        waited_too_long = (now - self._t_change) * 1000 + _EPSILON_MS >= limit
        if stable:
            self.state = ZoneState.SHOWN
            self._shown = mask
            return RunOcr(self._t_change)
        if waited_too_long:
            t_change = self._t_change
            if self._growing:
                # read mid-typing: stay CHANGING so the zone is read again once typing
                # stops, and the word cut now is not lost even if nothing else appears
                self._shown = mask
                self._begin_change(now, growing=True)
                return RunOcr(t_change, still_typing=True)
            self.state = ZoneState.SHOWN
            self._shown = mask
            return RunOcr(t_change)
        return None


def _added_removed(before: Mask, after: Mask) -> tuple[int, int]:
    added = int(np.count_nonzero(after & ~before))
    removed = int(np.count_nonzero(before & ~after))
    return added, removed
