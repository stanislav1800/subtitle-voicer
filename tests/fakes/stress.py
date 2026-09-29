"""Fake StressMarkers."""

from __future__ import annotations

import re

_WORD = re.compile(r"[А-Яа-яЁё]+")
_VOWEL = re.compile(r"[аеёиоуыэюяАЕЁИОУЫЭЮЯ]")


class FirstVowelStress:
    """Stresses the first vowel of every Cyrillic word without "+", like silero-stress
    leaves stressed words alone."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def mark(self, text: str) -> str:
        self.calls.append(text)

        def stress(m: re.Match[str]) -> str:
            word = m.group(0)
            start, end = m.span()
            if "+" in text[max(start - 1, 0) : end + 1]:
                return word
            return _VOWEL.sub(lambda v: "+" + v.group(0), word, count=1)

        return _WORD.sub(stress, text)


class OverwritingStress(FirstVowelStress):
    """A marker that strips existing "+" and stresses everything anew."""

    def mark(self, text: str) -> str:
        return super().mark(text.replace("+", ""))
