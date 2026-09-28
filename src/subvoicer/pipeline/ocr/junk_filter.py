"""Junk filter for OCR results (TZ 4.3)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

_PUNCTUATION_RE = re.compile(r"[\W_]+", re.UNICODE)


class JunkReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    NOT_CYRILLIC = "not_cyrillic"
    TOO_SHORT = "too_short"


@dataclass(frozen=True, slots=True)
class JunkVerdict:
    reason: JunkReason | None

    @property
    def ok(self) -> bool:
        return self.reason is None


class JunkFilter:
    def __init__(
        self,
        short_words: Iterable[str],
        *,
        min_confidence: float = 0.6,
        min_cyrillic_ratio: float = 0.6,
        min_length: int = 3,
    ) -> None:
        self._short_words = {_bare(word) for word in short_words}
        self._min_confidence = min_confidence
        self._min_cyrillic_ratio = min_cyrillic_ratio
        self._min_length = min_length

    def check(self, text: str, mean_confidence: float) -> JunkVerdict:
        if mean_confidence < self._min_confidence:
            return JunkVerdict(JunkReason.LOW_CONFIDENCE)
        if cyrillic_ratio(text) < self._min_cyrillic_ratio:
            return JunkVerdict(JunkReason.NOT_CYRILLIC)
        bare = _bare(text)
        if len(bare.replace(" ", "")) < self._min_length and bare not in self._short_words:
            return JunkVerdict(JunkReason.TOO_SHORT)
        return JunkVerdict(None)


def cyrillic_ratio(text: str) -> float:
    """Share of Cyrillic letters among all letters; 0.0 when there are no letters."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ") / len(letters)


def _bare(text: str) -> str:
    """Lower case, punctuation removed, whitespace collapsed."""
    return " ".join(_PUNCTUATION_RE.sub(" ", text.lower()).split())
