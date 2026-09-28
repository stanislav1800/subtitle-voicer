"""Processing of a recognised line for ANY text source (TZ 4.4, steps 0-6).

Knows nothing about masks or zones: it gets a text, the channel it came from and the
``was_empty`` flag, and decides whether this is a new line, a continuation, or nothing.
The junk filter (step 1) runs before this module.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from rapidfuzz.fuzz import ratio

from subvoicer.pipeline.normalize import normalize, raw_tail

CROSS_SOURCE_WINDOW_S = 3.0
CONTINUATION_MIN_GROWTH = 3


class DedupKind(StrEnum):
    NEW = "new"
    CONTINUATION = "continuation"
    SAME = "same"  # step 5: the line is still on screen
    RECENT = "recent"  # step 2: voiced from this channel within repeat_cooldown_min
    DUPLICATE = "duplicate"  # step 0: the same text just came from another source


@dataclass(frozen=True, slots=True)
class DedupResult:
    kind: DedupKind
    text: str = ""  # what to voice: the whole line for NEW, only the new tail for CONTINUATION
    used_was_empty: bool = False  # step 3 consumed the flag; the source must reset it

    @property
    def voiced(self) -> bool:
        return self.kind in (DedupKind.NEW, DedupKind.CONTINUATION)


@dataclass
class _Channel:
    last: str | None = None  # last voiced line (original text)
    last_norm: str = ""
    voiced_at: dict[str, float] = field(default_factory=dict)  # normalised text -> time


class Deduplicator:
    def __init__(
        self, match_ratio: float = 90, cross_source_window_s: float = CROSS_SOURCE_WINDOW_S
    ):
        self.match_ratio = match_ratio
        self.cross_source_window_s = cross_source_window_s
        self._channels: dict[str, _Channel] = {}
        self._recent: deque[tuple[float, str, str]] = deque()  # (time, channel, normalised)

    def process(
        self,
        text: str,
        channel: str,
        *,
        was_empty: bool,
        now: float,
        repeat_cooldown_min: float = 0,
    ) -> DedupResult:
        norm = normalize(text)
        state = self._channels.setdefault(channel, _Channel())

        # 0. the same text from another source within the last few seconds
        self._forget_old(now)
        for _, other, other_norm in self._recent:
            if other != channel and self._same(norm, other_norm):
                return DedupResult(DedupKind.DUPLICATE)

        # 2. voiced from this channel recently
        if repeat_cooldown_min > 0:
            horizon = now - repeat_cooldown_min * 60
            state.voiced_at = {t: at for t, at in state.voiced_at.items() if at >= horizon}
            if any(self._same(norm, seen) for seen in state.voiced_at):
                return DedupResult(DedupKind.RECENT)
        else:
            state.voiced_at.clear()

        # 3. first text after an empty zone is always a new line
        if was_empty:
            self._remember(state, channel, text, norm, now)
            return DedupResult(DedupKind.NEW, text.strip(), used_was_empty=True)

        if state.last is not None and state.last_norm:
            last_norm = state.last_norm
            # 4. the line grew: voice only the tail
            if len(norm) > len(last_norm) + CONTINUATION_MIN_GROWTH and self._same(
                norm[: len(last_norm)], last_norm
            ):
                tail = raw_tail(text, len(last_norm))
                self._remember(state, channel, text, norm, now)
                return DedupResult(DedupKind.CONTINUATION, tail)
            # 5. still the same line
            if self._same(norm, last_norm):
                return DedupResult(DedupKind.SAME)

        # 6. anything else is a new line
        self._remember(state, channel, text, norm, now)
        return DedupResult(DedupKind.NEW, text.strip())

    def _same(self, a: str, b: str) -> bool:
        return ratio(a, b) >= self.match_ratio

    def _remember(self, state: _Channel, channel: str, text: str, norm: str, now: float) -> None:
        state.last = text
        state.last_norm = norm
        state.voiced_at[norm] = now
        self._recent.append((now, channel, norm))

    def _forget_old(self, now: float) -> None:
        while self._recent and now - self._recent[0][0] > self.cross_source_window_s:
            self._recent.popleft()
