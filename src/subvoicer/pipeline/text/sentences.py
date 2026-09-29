"""Splitting text into pieces for streaming synthesis (TZ 4.7, step 7).

Every sentence is a piece, long sentences are split further at commas, semicolons and
dashes: the first sound comes sooner, and a line that has to hurry (TZ 4.9, ``speedup``)
changes speed from the next piece on.
"""

from __future__ import annotations

import re

LONG_SENTENCE = 90  # characters; longer sentences are split into clauses
MIN_CLAUSE = 25  # clauses shorter than this stick to the next one

# after . ! ? always; after a pause "…" only when a new sentence starts with a capital
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)|(?<=…)\s+(?=\+?[A-ZА-ЯЁ])")
_CLAUSE = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–]\s)")


def split_sentences(text: str) -> list[str]:
    pieces: list[str] = []
    for sentence in (p.strip() for p in _BOUNDARY.split(text)):
        if not sentence:
            continue
        if len(sentence) <= LONG_SENTENCE:
            pieces.append(sentence)
        else:
            pieces.extend(_clauses(sentence))
    return pieces


def _clauses(sentence: str) -> list[str]:
    result: list[str] = []
    current = ""
    for part in _CLAUSE.split(sentence):
        current = f"{current} {part}".strip() if current else part.strip()
        if len(current) >= MIN_CLAUSE:
            result.append(current)
            current = ""
    if current:
        if result and len(current) < MIN_CLAUSE:
            result[-1] = f"{result[-1]} {current}"
        else:
            result.append(current)
    return result
