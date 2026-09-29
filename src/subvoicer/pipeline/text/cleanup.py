"""Cleanup before synthesis (TZ 4.7, step 6): OCR junk, stray symbols, ellipses -> pause."""

from __future__ import annotations

import re

PAUSE = "…"  # the engines turn it into a short pause

_JUNK = re.compile(r"[|_~\\/^*#<>{}\[\]=@$`]")
_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…+)\s*")
# a lone character between spaces that is neither a letter/digit nor sentence punctuation
_STRAY = re.compile(r"(?<!\S)[^\w\s.,!?:;«»\"'()—–\-…+](?!\S)")
_SPACES = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?:;])")


def clean(text: str) -> str:
    text = _JUNK.sub(" ", text)
    text = _STRAY.sub(" ", text)
    text = _ELLIPSIS.sub(f" {PAUSE} ", text)
    text = _SPACES.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return text.strip().strip(PAUSE).strip()  # a pause at the edges only delays speech
