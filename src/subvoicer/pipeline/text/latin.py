"""Latin letters (TZ 4.7, step 4): abbreviations and single capital letters by English
letter names ("нажмите Q" -> "кью"), other words by a simple transliteration table.
Mixed words are OCR look-alikes and become Cyrillic."""

from __future__ import annotations

import re
from collections.abc import Mapping

_ABBREVIATION = re.compile(r"(?<![\w])[A-Z]{2,5}(?![\w])")
_LETTER = re.compile(r"(?<![\w])[A-Z](?![\w])")  # "нажмите Q"
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
        text = _LETTER.sub(self._abbreviation, text)
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


# Latin letters OCR gives for Cyrillic ones of a game font (italic «r» for «г», «u» for «и»)
TWINS = "ABCEHKMOPTXYaceopxyru"
TWIN_HOMOGLYPHS = str.maketrans(TWINS, "АВСЕНКМОРТХУасеорхуги")
_ANY_LETTER = re.compile(r"[^\W\d_]+")


def twins_to_cyrillic(text: str) -> str:
    """Russian read with Latin twins, before the junk filter: a word mixing the scripts
    («Tак»), or a whole line of twins only — «HeT», «Xa-xa-xa!», «Y-y! Ara!» were dropped as
    not Cyrillic, so short lines went unvoiced. A line with any other Latin letter («REMEDY»,
    «Children of…») stays as it is."""
    latin = [ch for ch in text if "A" <= ch <= "z" and ch.isalpha()]
    if not latin:
        return text
    if not _HAS_CYRILLIC.search(text) and all(ch in TWINS for ch in latin):
        return _ANY_LETTER.sub(_word_case, text.translate(TWIN_HOMOGLYPHS))

    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        mixed = _HAS_CYRILLIC.search(word) and any("A" <= ch <= "z" for ch in word)
        if mixed and all(ch in TWINS for ch in word if "A" <= ch <= "z"):
            return _word_case(word.translate(TWIN_HOMOGLYPHS))
        return word

    return _ANY_LETTER.sub(fix, text)


def _word_case(word: str | re.Match[str]) -> str:
    """«НеТ» -> «Нет»: a capital twin inside a lower-case word is a small letter (the small
    Cyrillic «т» of many fonts looks like «T»)."""
    text = word if isinstance(word, str) else word.group(0)
    if len(text) > 1 and any(ch.islower() for ch in text[1:]):
        return text[0] + text[1:].lower()
    return text
