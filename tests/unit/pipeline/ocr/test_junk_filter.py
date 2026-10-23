import pytest

from subvoicer.config.resources import short_words
from subvoicer.pipeline.ocr.junk_filter import JunkFilter, JunkReason, cyrillic_ratio


@pytest.fixture
def junk() -> JunkFilter:
    return JunkFilter(short_words())


def test_normal_phrase_passes(junk: JunkFilter) -> None:
    assert junk.check("Встретимся у моста.", 0.9).ok


def test_low_confidence(junk: JunkFilter) -> None:
    assert junk.check("Встретимся у моста.", 0.59).reason is JunkReason.LOW_CONFIDENCE
    assert junk.check("Встретимся у моста.", 0.6).ok


def test_latin_garbage(junk: JunkFilter) -> None:
    assert junk.check("lIl ll1 Il", 0.9).reason is JunkReason.NOT_CYRILLIC


def test_some_latin_is_fine(junk: JunkFilter) -> None:
    assert junk.check("Включи GPS, иначе заблудимся.", 0.9).ok


def test_no_letters_is_junk(junk: JunkFilter) -> None:
    assert junk.check("12:30", 0.9).reason is JunkReason.NOT_CYRILLIC


def test_too_short(junk: JunkFilter) -> None:
    assert junk.check("Жп", 0.9).reason is JunkReason.TOO_SHORT


@pytest.mark.parametrize("text", ["Да.", "НЕТ!", "Ну?", "я", "— Ок...", "Ага"])
def test_allowed_short_words(junk: JunkFilter, text: str) -> None:
    assert junk.check(text, 0.9).ok


def test_three_letters_are_enough(junk: JunkFilter) -> None:
    assert junk.check("Кто", 0.9).ok


def test_cyrillic_ratio() -> None:
    assert cyrillic_ratio("abАБ") == 0.5
    assert cyrillic_ratio("...") == 0.0


def test_short_words_resource_matches_tz() -> None:
    expected = {
        "да",
        "нет",
        "ой",
        "ах",
        "эй",
        "ну",
        "ага",
        "угу",
        "ого",
        "ха",
        "ок",
        "я",
        "ты",
        "мы",
    }
    assert set(short_words()) == expected


def test_a_russian_line_with_a_long_english_name_is_kept() -> None:
    """TZ 1.39: English letters outnumber the Russian ones, two Russian words are there."""
    junk = JunkFilter([])
    assert junk.check("Это песня Never Fade Away.", 0.95).ok
    assert junk.check("Позвони в Militech Corporation, срочно.", 0.95).ok
    for english in ("Wake up, Samurai. We have a city to burn.", "LIMITED ACTION", "Ok, да"):
        assert junk.check(english, 0.95).reason is JunkReason.NOT_CYRILLIC
