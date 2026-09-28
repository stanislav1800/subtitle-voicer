"""Normalised text for comparisons (TZ 4.4): lower case, ё -> е, no punctuation, single spaces."""

from __future__ import annotations

import re

_LEADING_SEPARATORS = re.compile(r"^[\W_]+")


def _is_word_char(ch: str) -> bool:
    return ch.isalnum()


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalise and return, for every normalised character, its index in ``text``.

    Lets a prefix length measured on normalised text be mapped back to the original
    (e.g. to cut the new tail of a growing line with its punctuation intact).
    """
    out: list[str] = []
    index: list[int] = []
    separator_at: int | None = None  # first non-word character after the last word
    for i, ch in enumerate(text):
        if _is_word_char(ch):
            if separator_at is not None and out:
                out.append(" ")
                index.append(separator_at)
            separator_at = None
            low = ch.lower()
            out.append("е" if low == "ё" else low)
            index.append(i)
        elif separator_at is None:
            separator_at = i
    return "".join(out), index


def raw_tail(text: str, normalized_prefix_len: int) -> str:
    """The part of ``text`` after its first ``normalized_prefix_len`` normalised characters,
    without leading separators (", повторять" -> "повторять")."""
    norm, index = normalize_with_map(text)
    if normalized_prefix_len <= 0:
        return text.strip()
    if normalized_prefix_len >= len(norm):
        return ""
    end_of_prefix = index[normalized_prefix_len - 1] + 1
    return _LEADING_SEPARATORS.sub("", text[end_of_prefix:]).strip()
