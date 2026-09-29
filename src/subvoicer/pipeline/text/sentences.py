"""Splitting long text into sentences for streaming synthesis (TZ 4.7, step 7)."""

from __future__ import annotations

import re

MAX_SENTENCES_WHOLE = 2  # up to this many sentences are synthesized as one piece

# after . ! ? always; after a pause "…" only when a new sentence starts with a capital
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)|(?<=…)\s+(?=\+?[A-ZА-ЯЁ])")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _BOUNDARY.split(text) if p.strip()]
    if len(parts) <= MAX_SENTENCES_WHOLE:
        return [text.strip()] if text.strip() else []
    return parts
