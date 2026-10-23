"""Is a candidate a plausible speaker name? (TZ 4.5)"""

from __future__ import annotations

from collections.abc import Iterable

from subvoicer.pipeline.normalize import normalize

MIN_LENGTH, MAX_LENGTH = 2, 30
MAX_WORDS = 3


class NameValidator:
    def __init__(self, stoplist: Iterable[str]) -> None:
        self._stoplist = {normalize(word) for word in stoplist}

    def is_name(self, candidate: str, *, digits: bool = False) -> bool:
        """``digits``: a name in brackets may have them («[ П-3 ]», TZ 1.33)."""
        name = candidate.strip()
        words = name.split()
        return (
            1 <= len(words) <= MAX_WORDS
            and MIN_LENGTH <= len(name) <= MAX_LENGTH
            and name[0].isupper()
            and (digits or not any(ch.isdigit() for ch in name))  # "Встретимся в 22:30"
            and not any(ch in ".!?…" for ch in name)  # "Он вышел. Джеки: …" is not a name
            and normalize(name) not in self._stoplist
        )
