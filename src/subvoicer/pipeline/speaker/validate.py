"""Is a candidate a plausible speaker name? (TZ 4.5)"""

from __future__ import annotations

from collections.abc import Iterable

from subvoicer.pipeline.normalize import normalize

MIN_LENGTH, MAX_LENGTH = 2, 30
MAX_WORDS = 3
MAX_NUMBER = 3  # digits of a number after a name: «Житель 2», «Незнакомец 12»


def numbered(name: str) -> tuple[str, str] | None:
    """``("Житель", "2")`` for a name with a number as its own last word (TZ 1.41), else
    None: RDR2 calls people of a scene «Житель 2», «Незнакомец 3»."""
    words = name.split()
    if len(words) < 2 or not words[-1].isdigit() or len(words[-1]) > MAX_NUMBER:
        return None
    return " ".join(words[:-1]), words[-1]


class NameValidator:
    def __init__(self, stoplist: Iterable[str]) -> None:
        self._stoplist = {normalize(word) for word in stoplist}

    def is_name(self, candidate: str, *, digits: bool = False) -> bool:
        """``digits``: a name in brackets may have them anywhere («[ П-3 ]», TZ 1.33); any
        name may end with a number as a word of its own («Житель 2», TZ 1.41) — the name
        before it passes the same rules («Совет 2» is in the stop-list as «Совет»)."""
        name = candidate.strip()
        words = name.split()
        number = numbered(name)
        base = number[0] if number is not None else name
        return (
            1 <= len(words) <= MAX_WORDS
            and MIN_LENGTH <= len(name) <= MAX_LENGTH
            and name[0].isupper()
            and (digits or not any(ch.isdigit() for ch in base))  # "Встретимся в 22:30"
            and not any(ch in ".!?…" for ch in name)  # "Он вышел. Джеки: …" is not a name
            and normalize(name) not in self._stoplist
            and normalize(base) not in self._stoplist
        )
