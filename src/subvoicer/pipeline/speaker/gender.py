"""Gender guess from the line's own words (TZ 4.5): "я пошла" / "я пошёл", "я рада" / "я рад"."""

from __future__ import annotations

import re
from collections.abc import Iterable

from subvoicer.core.models import Gender

_F = re.IGNORECASE

_FEMALE = [
    re.compile(r"\bя\s+(\w+)(ла|лась)\b", _F),
    re.compile(r"\b(\w+)(ла|лась)\s+я\b", _F),
    re.compile(r"\bя\s+(рада|готова|должна|уверена|согласна|одна|сама|виновата|знакома)\b", _F),
]
_MALE_VERBS = [
    re.compile(r"\bя\s+(\w+)(л|лся)\b", _F),
    re.compile(r"\b(\w+)(л|лся)\s+я\b", _F),
]
_MALE_WORDS = re.compile(r"\bя\s+(рад|готов|должен|уверен|согласен|один|сам|виноват|знаком)\b", _F)


class GenderGuesser:
    def __init__(self, exceptions: Iterable[str]) -> None:
        """``exceptions``: nouns ending in «-л» that are not past-tense verbs."""
        self._exceptions = {_norm(w) for w in exceptions}

    def guess(self, text: str) -> Gender:
        female = any(p.search(text) for p in _FEMALE)
        male = bool(_MALE_WORDS.search(text)) or any(
            _norm(m.group(1) + m.group(2)) not in self._exceptions
            for p in _MALE_VERBS
            for m in p.finditer(text)
        )
        if female == male:
            return Gender.UNKNOWN
        return Gender.FEMALE if female else Gender.MALE


def _norm(word: str) -> str:
    return word.lower().replace("ё", "е")
