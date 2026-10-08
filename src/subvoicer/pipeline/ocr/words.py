"""Fixing OCR words by a dictionary of word forms (TZ 1.23).

Some game fonts make OCR mix up letters (Cyberpunk 2077: "а" and "о" — "токое", "нодо").
For every word: not in the dictionary -> the variant with the fewest swapped letters (then the
most frequent); in the dictionary but rare while a swapped variant is far more common -> that
variant; still unknown -> split a glued pair of words. Dictionary lookups only: well under a
millisecond per line.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Mapping, Sequence

WORD = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")
MAX_SPOTS = 8  # 2**8 variants at most per word
RARE_RATIO = 50  # a known word gives way to a variant this many times more frequent
SPLIT_MIN_COUNT = 1000  # both halves of a split glued word must be this common
# a half of one or two letters only if it is a word that glues in subtitles ("онживёт",
# "нелюбит"); otherwise "Фотово" (Г read as Ф) would become "Фото во"
SHORT_GLUE = frozenset(
    [
        "я",
        "не",
        "ни",
        "он",
        "мы",
        "вы",
        "ты",
        "же",
        "ли",
        "бы",
        "да",
        "до",
        "на",
        "по",
        "за",
        "из",
        "от",
        "об",
        "но",
        "а",
        "и",
        "в",
        "с",
        "к",
        "у",
        "о",
    ]
)
DEFAULT_CONFUSIONS: tuple[tuple[str, str], ...] = (("а", "о"),)


def key(word: str) -> str:
    return word.lower().replace("ё", "е")


class WordFixer:
    def __init__(
        self,
        counts: Mapping[str, int],
        confusions: Sequence[tuple[str, str]] = DEFAULT_CONFUSIONS,
        known: Iterable[str] = (),
    ) -> None:
        """``counts``: word form (lower case, ``ё`` as ``е``) -> frequency. ``known``: names of
        the profile's characters — treated as the most frequent words."""
        self._counts = counts
        self._known = {key(w): max(counts.values(), default=1) for w in known if w}
        self._swap: dict[str, str] = {}
        for a, b in confusions:
            self._swap[key(a)] = key(b)
            self._swap[key(b)] = key(a)

    def count(self, word: str) -> int:
        return self._known.get(word) or self._counts.get(word, 0)

    def fix(self, text: str) -> str:
        return WORD.sub(lambda m: self.fix_word(m.group(0)), text)

    def fix_word(self, word: str) -> str:
        low = key(word)
        own = self.count(low)
        if own == 0:
            better = self._closest(low)
            if better is not None:
                return _cased(word, better)
            split = self._split(low)
            return _cased(word, split) if split is not None else word
        if low in self._known:
            return word
        rarest_allowed = own * RARE_RATIO
        best = max(
            ((self.count(v), v) for n, v in self._variants(low) if n),
            default=(0, low),
        )
        return _cased(word, best[1]) if best[0] >= rarest_allowed else word

    def _variants(self, low: str) -> Iterable[tuple[int, str]]:
        """(number of swaps, variant) for every combination of swappable letters."""
        spots = [i for i, ch in enumerate(low) if ch in self._swap]
        if not spots or len(spots) > MAX_SPOTS:
            return
        for flips in itertools.product((False, True), repeat=len(spots)):
            chars = list(low)
            for i, flip in zip(spots, flips, strict=True):
                if flip:
                    chars[i] = self._swap[chars[i]]
            yield sum(flips), "".join(chars)

    def _closest(self, low: str) -> str | None:
        found = [(n, -self.count(v), v) for n, v in self._variants(low) if self.count(v)]
        return min(found)[2] if found else None

    def _known_or_closest(self, low: str) -> str | None:
        return low if self.count(low) else self._closest(low)

    def _split(self, low: str) -> str | None:
        if "-" in low:
            return None
        best: tuple[int, str] | None = None
        for i in range(2, len(low) - 1):
            left, right = self._known_or_closest(low[:i]), self._known_or_closest(low[i:])
            if left is None or right is None:
                continue
            if any(len(half) < 3 and half not in SHORT_GLUE for half in (left, right)):
                continue
            score = min(self.count(left), self.count(right))
            if score >= SPLIT_MIN_COUNT and (best is None or score > best[0]):
                best = (score, f"{left} {right}")
        return best[1] if best is not None else None


def _cased(original: str, fixed: str) -> str:
    """``fixed`` with the case (and ``ё``) of ``original``; a split adds one space."""
    out = []
    j = 0
    for ch in fixed:
        if ch == " " and (j >= len(original) or original[j] != " "):
            out.append(" ")
            continue
        source = original[j] if j < len(original) else ch
        letter = "ё" if source.lower() == "ё" and ch == "е" else ch
        out.append(letter.upper() if source.isupper() else letter)
        j += 1
    return "".join(out)
