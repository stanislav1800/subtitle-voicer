"""«Не озвучивать» (TZ 4.2, 1.33): phrases of a game that are never voiced — on-screen hints
(«[E] Взять», «Влезть») that a dark scene makes look like subtitles."""

from __future__ import annotations

from collections.abc import Iterable

from subvoicer.pipeline.normalize import normalize
from subvoicer.pipeline.text.latin import TWIN_HOMOGLYPHS


def skip_key(text: str) -> str:
    """Compared without case, punctuation and brackets; Latin twins count as Cyrillic, so
    «[E] Взять» read by OCR is the same as «[Е] Взять» typed by the user."""
    return normalize(text.translate(TWIN_HOMOGLYPHS))


class SkipList:
    def __init__(self, phrases: Iterable[str]) -> None:
        keys = {skip_key(phrase) for phrase in phrases}
        self._keys = sorted((k for k in keys if k), key=len, reverse=True)

    def __bool__(self) -> bool:
        return bool(self._keys)

    def match(self, text: str) -> str | None:
        """The phrase ``text`` starts with, by whole words, or None: «[Е] Взять» skips
        «[E] Взять патроны», but not «Взятьё» or «Он сказал: взять»."""
        key = skip_key(text)
        for phrase in self._keys:
            if key == phrase or key.startswith(phrase + " "):
                return phrase
        return None
