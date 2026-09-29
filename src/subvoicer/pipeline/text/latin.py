"""Latin letters (TZ 4.7, step 4): abbreviations by English letter names, other words by
a simple transliteration table. Mixed words are OCR look-alikes and become Cyrillic."""

from __future__ import annotations

import re
from collections.abc import Mapping

_ABBREVIATION = re.compile(r"(?<![\w])[A-Z]{2,5}(?![\w])")
_LATIN_WORD = re.compile(r"[A-Za-z]+")
_WORD = re.compile(r"\w+")
_HAS_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_ALL_HOMOGLYPHS = re.compile(r"[ABCEHKMOPTXaceopxyi]+")

# OCR confuses these Latin letters with Cyrillic ones inside Russian words ("Hy", "cумка")
HOMOGLYPHS = str.maketrans("ABCEHKMOPTXaceopxyi", "АВСЕНКМОРТХасеорхуі")
_UKRAINIAN_I = str.maketrans("іІ", "иИ")


class LatinReader:
    def __init__(self, letter_names: Mapping[str, str], translit: Mapping[str, str]) -> None:
        self._letters = {k.lower(): v for k, v in letter_names.items()}
        keys = sorted(translit, key=len, reverse=True)
        self._translit = {k.lower(): translit[k] for k in keys}
        self._translit_re = re.compile(
            "|".join(re.escape(k) for k in self._translit), re.IGNORECASE
        )

    def read(self, text: str) -> str:
        russian = _mostly_cyrillic(text)
        text = _WORD.sub(lambda m: self._fix_lookalikes(m, russian), text)
        text = _ABBREVIATION.sub(self._abbreviation, text)
        return _LATIN_WORD.sub(self._transliterate, text)

    def _fix_lookalikes(self, m: re.Match[str], russian: bool) -> str:
        """OCR look-alikes: a word mixing Latin and Cyrillic ("Tак"), or, in Russian text,
        a word made only of look-alike letters that is not an abbreviation ("Hy")."""
        word = m.group(0)
        mixed = bool(_HAS_CYRILLIC.search(word) and _LATIN_WORD.search(word))
        lookalike = (
            russian
            and _ALL_HOMOGLYPHS.fullmatch(word) is not None
            and not _ABBREVIATION.fullmatch(word)
        )
        if mixed or lookalike:
            return word.translate(HOMOGLYPHS).translate(_UKRAINIAN_I)
        return word

    def _abbreviation(self, m: re.Match[str]) -> str:
        return " ".join(self._letters.get(ch.lower(), ch) for ch in m.group(0))

    def _transliterate(self, m: re.Match[str]) -> str:
        word = m.group(0)
        result = self._translit_re.sub(lambda t: self._translit[t.group(0).lower()], word)
        return result.capitalize() if word[0].isupper() else result


def _mostly_cyrillic(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and len(_HAS_CYRILLIC.findall(text)) * 2 > len(letters)
