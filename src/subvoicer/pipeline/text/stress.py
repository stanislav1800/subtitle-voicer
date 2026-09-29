"""Stresses and homographs through the StressMarker port (TZ 4.7, step 5).

silero-stress leaves words that already contain "+" untouched (checked, docs/DECISIONS.md).
The guard here keeps that promise for any marker: if a word with a manual "+" comes back
changed, the text is marked again with such words hidden behind placeholders.
"""

from __future__ import annotations

import re

from subvoicer.core.ports import StressMarker

_STRESSED_WORD = re.compile(r"\w*\+\w+")
_PLACEHOLDER = "ЪЪ{}ЪЪ"  # no vowels: a marker never puts a stress inside
_PLACEHOLDER_RE = re.compile(r"ЪЪ(\d+)ЪЪ")


def add_stress(text: str, marker: StressMarker) -> str:
    if not text.strip():
        return text
    manual = _STRESSED_WORD.findall(text)
    marked = marker.mark(text)
    kept = _STRESSED_WORD.findall(marked)
    if all(word in kept for word in manual):
        return marked
    return _with_placeholders(text, marker)


def _with_placeholders(text: str, marker: StressMarker) -> str:
    hidden: list[str] = []

    def hide(m: re.Match[str]) -> str:
        hidden.append(m.group(0))
        return _PLACEHOLDER.format(len(hidden) - 1)

    marked = marker.mark(_STRESSED_WORD.sub(hide, text))
    return _PLACEHOLDER_RE.sub(lambda m: hidden[int(m.group(1))], marked)
