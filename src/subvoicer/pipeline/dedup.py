"""Processing of a recognised line for ANY text source (TZ 4.4, steps 0-6).

Knows nothing about masks or zones: it gets a text, the channel it came from and the
``was_empty`` flag, and decides whether this is a new line, a continuation, or nothing.
The junk filter (step 1) runs before this module.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from rapidfuzz.distance import LCSseq
from rapidfuzz.fuzz import partial_ratio, ratio

from subvoicer.pipeline.normalize import normalize, raw_tail

CROSS_SOURCE_WINDOW_S = 3.0
CONTINUATION_MIN_GROWTH = 3
FRAGMENT_MIN_CHARS = 12  # a shorter piece may match by chance (TZ 1.22)
REVEALED_MIN_SHARE = 0.4  # the earlier piece covers at least this much of the whole line
# TZ 1.27: a worse reading of a line still on screen (letters lost to a noisy background):
# this share of its letters is found in the line, in order, and it covers this much of it
SPLIT_WINDOW_S = 15.0  # the second half of a split line comes within this after the first
DEGRADED_MATCH = 0.78
# TZ 1.37: the same subtitle read again while the zone never went empty — a blurry video
# frame reads «Ладно,яскжутебе» and then «Лодняскжутебе»: of 47 such readings in letsplay
# logs all were 74.6+ alike, of 109 different lines in a row none above 54.3; a short line
# keeps match_ratio («Ты кто?» / «Ты что?»)
REREAD_RATIO = 70
REREAD_MIN_LETTERS = 12
REREAD_LENGTH = 0.7  # about as long: a line added above or below is not a re-read (0.54)
DEGRADED_MIN_SHARE = 0.4


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


def on_screen(text: str, previous: Sequence[str], match_ratio: float = 90) -> bool:
    """TZ 1.22: ``text`` is a line of the previous reading still on screen — the same line
    (any speaker: OCR mixes up names) or a visible part of it (the top cut by the zone edge,
    the line moving up as the next one appears below) — or that line with its cut top
    revealed again (the earlier piece is the end of ``text``) — or a worse reading of that line
    (TZ 1.27): the background behind a still subtitle flickers, the zone is read again and
    OCR loses letters («Администратор Уоико о Здрав вуи е…»); its letters, in order, are
    almost all in the line."""
    norm = normalize(text)
    if not norm:
        return False
    for line in previous:
        before = normalize(line)
        if ratio(norm, before) >= match_ratio:
            return True
        if (
            FRAGMENT_MIN_CHARS <= len(norm) <= len(before)
            and partial_ratio(norm, before) >= match_ratio
        ):
            return True
        if (
            FRAGMENT_MIN_CHARS <= len(before) < len(norm)
            and len(before) >= REVEALED_MIN_SHARE * len(norm)
            and ratio(norm[-len(before) :], before) >= match_ratio
        ):
            return True
        if degraded(norm, before):
            return True
    return False


def degraded(norm: str, before: str) -> bool:
    """``norm`` is ``before`` with letters lost or misread (both normalised): spaces aside,
    most of its letters appear in ``before`` in the same order, and it is not a short piece
    that matches by chance. Not longer than ``before``: a better reading after a bad one is
    not "worse" — it is voiced."""
    letters, line = norm.replace(" ", ""), before.replace(" ", "")
    return (
        len(letters) >= FRAGMENT_MIN_CHARS
        and DEGRADED_MIN_SHARE * len(line) <= len(letters) <= len(line)
        and LCSseq.similarity(letters, line) >= DEGRADED_MATCH * len(letters)
    )


def grown_from(text: str, previous: Sequence[str], match_ratio: float = 90) -> str | None:
    """TZ 1.22: the new end of ``text`` when it is a line of the previous reading that grew
    (typing went on), else None."""
    norm = normalize(text)
    for line in previous:
        before = normalize(line)
        if (
            before
            and len(norm) > len(before) + CONTINUATION_MIN_GROWTH
            and ratio(norm[: len(before)], before) >= match_ratio
        ):
            return raw_tail(text, len(before))
    return None


def continues(previous: str, text: str) -> bool:
    """TZ 1.28: ``text`` is the second half of a line the game split in two subtitles — the
    first half stops mid-sentence (no ``.``, ``!`` or ``?`` at the end; an ellipsis does not
    end a sentence when the next part starts with a small letter) and ``text`` starts with
    a small letter: «…и я не» + «мог выдавить из себя ни строчки.»"""
    first = next((ch for ch in text if ch.isalpha()), "")
    if not first or not first.islower():
        return False
    end = previous.rstrip(" »\"')")
    if not end:
        return False
    return end.endswith(("..", "…")) or end[-1] not in ".!?"


class Deduplicator:
    def __init__(
        self,
        match_ratio: float = 90,
        cross_source_window_s: float = CROSS_SOURCE_WINDOW_S,
        reread_ratio: float = REREAD_RATIO,
    ):
        self.match_ratio = match_ratio
        self.reread_ratio = reread_ratio
        self.cross_source_window_s = cross_source_window_s
        self._channels: dict[str, _Channel] = {}
        self._recent: deque[tuple[float, str, str]] = deque()  # (time, channel, normalised)

    def same_as_last(self, text: str, channel: str) -> bool:
        """``text`` matches the last voiced line of ``channel`` (step 5 comparison)."""
        state = self._channels.get(channel)
        return (
            state is not None
            and state.last is not None
            and self._reread(normalize(text), state.last_norm)
        )

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
                if self._repeats_end(normalize(tail), last_norm):
                    # OCR read the same line a few letters longer ("потерялсвязь" ->
                    # "потерял связь"): the "tail" is the end of the line again (TZ 1.22)
                    return DedupResult(DedupKind.SAME)
                self._remember(state, channel, text, norm, now)
                return DedupResult(DedupKind.CONTINUATION, tail)
            # 5. still the same line, maybe read worse or better this time (TZ 1.37)
            if self._reread(norm, last_norm):
                return DedupResult(DedupKind.SAME)

        # 6. anything else is a new line
        self._remember(state, channel, text, norm, now)
        return DedupResult(DedupKind.NEW, text.strip())

    def _repeats_end(self, tail: str, last: str) -> bool:
        return bool(tail) and len(tail) < len(last) and self._same(tail, last[-len(tail) :])

    def _same(self, a: str, b: str) -> bool:
        return ratio(a, b) >= self.match_ratio

    def _reread(self, a: str, b: str) -> bool:
        """``a`` is the line ``b`` read again (no empty zone between, TZ 1.37)."""
        short, long = sorted((len(a.replace(" ", "")), len(b.replace(" ", ""))))
        loose = short >= REREAD_MIN_LETTERS and short >= REREAD_LENGTH * long
        return ratio(a, b) >= (self.reread_ratio if loose else self.match_ratio)

    def _remember(self, state: _Channel, channel: str, text: str, norm: str, now: float) -> None:
        state.last = text
        state.last_norm = norm
        state.voiced_at[norm] = now
        self._recent.append((now, channel, norm))

    def _forget_old(self, now: float) -> None:
        while self._recent and now - self._recent[0][0] > self.cross_source_window_s:
            self._recent.popleft()
