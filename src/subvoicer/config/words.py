"""The dictionary of word forms for fixing OCR words (TZ 1.23): ``models/words/ru_full.txt``,
lines ``word count`` sorted by count (FrequencyWords)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

MIN_COUNT = 3  # rarer forms are mostly typos of the subtitles themselves


def load_word_counts(path: Path, min_count: int = MIN_COUNT) -> dict[str, int]:
    """Word (lower case, ``ё`` as ``е``) -> frequency; empty if the file is missing."""
    if not path.is_file():
        log.warning("Нет словаря словоформ %s: слова не исправляются", path)
        return {}
    counts: dict[str, int] = {}
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            word, _, number = line.rstrip("\n").partition(" ")
            count = int(number or 0)
            if count < min_count:
                break  # the file is sorted by count
            word = word.lower().replace("ё", "е")
            counts[word] = counts.get(word, 0) + count
    log.info("Словарь словоформ: %d форм", len(counts))
    return counts
