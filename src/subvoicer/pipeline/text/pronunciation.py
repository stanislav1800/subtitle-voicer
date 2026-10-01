"""Pronunciation dictionary (TZ 4.7.1): whole words, word stems with ``*``, raw regexes."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

STAR = "*"


@dataclass(frozen=True, slots=True)
class PronunciationEntry:
    source: str  # "from" in the profile
    target: str  # "to"; "+" before a vowel marks the stress
    regex: bool = False


def _word_pattern(word: str) -> str:
    """Case-insensitive literal where е and ё match each other."""
    parts = []
    for ch in word:
        if ch.lower() in "её":
            parts.append("[её]")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class _Rule:
    pattern: re.Pattern[str]
    target: str
    star_target: bool
    template: bool = False  # regex entries may use group references (\1) in "to"

    def apply(self, text: str) -> str:
        if self.template:
            return self.pattern.sub(self.target, text)
        if not self.star_target:
            return self.pattern.sub(lambda _: self.target, text)
        stem = self.target[:-1]
        return self.pattern.sub(lambda m: stem + m.group("ending"), text)


class PronunciationDict:
    """Plain and stem entries first (longer ``from`` first), regex entries after them."""

    def __init__(self, entries: Iterable[PronunciationEntry] = ()) -> None:
        self.entries = list(entries)
        plain: list[PronunciationEntry] = []
        regexes: list[PronunciationEntry] = []
        for entry in self.entries:
            if not entry.source.strip():
                continue
            (regexes if entry.regex else plain).append(entry)
        plain.sort(key=lambda e: len(e.source.rstrip(STAR)), reverse=True)
        self._rules = [self._compile(e) for e in plain]
        self._rules += [_Rule(re.compile(e.source), e.target, False, True) for e in regexes]

    def __len__(self) -> int:
        return len(self._rules)

    def with_extra(self, extra: Iterable[PronunciationEntry]) -> PronunciationDict:
        """This dictionary plus ``extra`` entries that replace ones with the same ``from``
        (listening to an entry before it is saved, TZ 4.13.1)."""
        added = list(extra)
        replaced = {e.source.strip().lower() for e in added}
        kept = [e for e in self.entries if e.source.strip().lower() not in replaced]
        return PronunciationDict([*added, *kept])

    def apply(self, text: str) -> str:
        for rule in self._rules:
            text = rule.apply(text)
        return text

    @staticmethod
    def _compile(entry: PronunciationEntry) -> _Rule:
        source = entry.source.strip()
        if source.endswith(STAR):
            body = _word_pattern(source[:-1])
            pattern = rf"(?<!\w){body}(?P<ending>\w*)"
            return _Rule(
                re.compile(pattern, re.IGNORECASE), entry.target, entry.target.endswith(STAR)
            )
        pattern = rf"(?<!\w){_word_pattern(source)}(?!\w)"
        return _Rule(re.compile(pattern, re.IGNORECASE), entry.target, False)
