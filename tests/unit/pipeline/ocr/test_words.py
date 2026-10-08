"""Fixing OCR words by a dictionary (TZ 1.23) on a small hand-made dictionary."""

from __future__ import annotations

import pytest

from subvoicer.pipeline.ocr.words import WordFixer

COUNTS = {
    "такое": 50_000,
    "надо": 80_000,
    "начало": 9_000,
    "начала": 12_000,
    "так": 995_583,
    "ток": 484,
    "как": 1_174_339,
    "кок": 271,
    "там": 300_000,
    "том": 250_000,
    "держит": 5_000,
    "чип": 1_574,
    "же": 400_000,
    "чумба": 10,
    "хватит": 60_000,
    "все": 900_000,
    "не": 4_000_000,
    "любит": 23_082,
    "фото": 12_074,
    "во": 150_000,
    "ещё": 1,
}


@pytest.fixture
def fixer() -> WordFixer:
    return WordFixer(COUNTS, known=["Ти-Баг", "Ёринобу"])


@pytest.mark.parametrize(
    ("ocr", "fixed"),
    [
        ("Ну токое бывает", "Ну такое бывает"),  # rule 1: not a word
        ("Нодо идти", "Надо идти"),
        ("на ночало", "на начало"),  # the fewest swaps, not the most frequent ("начала")
        ("ток вот", "так вот"),  # rule 2: a rare word, the variant is 2000x more common
        ("кок будто", "как будто"),
        ("Том может быть", "Том может быть"),  # both common: left alone
        ("держитчип", "держит чип"),  # rule 3: a glued pair
        ("жечумба", "жечумба"),  # "чумба" too rare to split on
        ("нелюбит", "не любит"),  # a short half that does glue
        ("фотово", "фотово"),  # "во" does not glue: not "фото во"
        ("ХВОТИТ!", "ХВАТИТ!"),  # case kept
        ("Звони Ти-Бог", "Звони Ти-Баг"),  # a character name is a known word
        ("Ёринобу пришёл", "Ёринобу пришёл"),  # names are not touched
        ("Shift", "Shift"),
    ],
)
def test_fix(fixer: WordFixer, ocr: str, fixed: str) -> None:
    assert fixer.fix(ocr) == fixed


@pytest.mark.parametrize(
    ("ocr", "fixed"),
    [
        ("Ти-Бог: До. Иначе повредишь.", "Ти-Бог: Да. Иначе повредишь."),
        ("Круто, до?", "Круто, да?"),
        ("До, всё нормально.", "Да, всё нормально."),
        ("До ты что?", "Да ты что?"),
        ("Эй, до ничего, беспокоится", "Эй, да ничего, беспокоится"),
        ("До скорого. С утра до ночи. До сих пор.", "До скорого. С утра до ночи. До сих пор."),
        ("Переключись в редакторе но тепловой слой.", "Переключись в редакторе на тепловой слой."),
        ("Смотрю я но вас.", "Смотрю я на вас."),
        ("Хорош, но сойдёт. Но при одном условии.", "Хорош, но сойдёт. Но при одном условии."),
        ("в документах,но чип тут", "в документах,но чип тут"),
        ("займусь — но потом", "займусь — но потом"),
    ],
)
def test_no_na_do_da_by_context(ocr: str, fixed: str) -> None:
    """TZ 1.23: pairs of common words, by punctuation and the next word."""
    assert WordFixer({}).fix(ocr) == fixed


def test_context_rules_only_with_the_a_o_pair() -> None:
    assert WordFixer({}, confusions=[("г", "ф")]).fix("Круто, до?") == "Круто, до?"


def test_yo_is_kept_when_the_word_is_fixed() -> None:
    """The dictionary keys have ``е`` for ``ё`` (the loader does it); the text keeps its ``ё``."""
    assert WordFixer({"елка": 500}).fix("Ёлко") == "Ёлка"


def test_other_confusions() -> None:
    """Another font, another pair: г<->ф ("Фотово" -> "Готово"); а<->о alone does not help."""
    assert WordFixer({"готово": 10_000}).fix("Фотово") == "Фотово"
    fixer = WordFixer({"готово": 10_000}, confusions=[("г", "ф"), ("а", "о")])
    assert fixer.fix("Фотово") == "Готово"
