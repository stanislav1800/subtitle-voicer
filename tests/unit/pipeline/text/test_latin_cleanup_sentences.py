import pytest

from subvoicer.config.resources import letter_names, translit_table
from subvoicer.pipeline.text.cleanup import clean
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.sentences import split_sentences


@pytest.fixture
def latin() -> LatinReader:
    return LatinReader(letter_names(), translit_table())


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Это дело ведёт FBI.", "Это дело ведёт эф би ай."),
        ("Включи GPS", "Включи джи пи эс"),
        ("Отправь через VPN", "Отправь через ви пи эн"),
        ("Night City", "Нигхт Сити"),
        ("OK, давай", "оу кей, давай"),  # an abbreviation, not a look-alike
        ("shotgun", "шотгун"),
        ("Hy, давай", "Ну, давай"),  # OCR look-alikes inside Cyrillic text
        ("Tак всегда", "Так всегда"),
        ("cумка", "сумка"),
        ("Бoже правый", "Боже правый"),
    ],
)
def test_latin(latin: LatinReader, text: str, spoken: str) -> None:
    assert latin.read(text) == spoken


def test_single_capital_and_long_capitals_are_words(latin: LatinReader) -> None:
    assert latin.read("Нажмите Q") == "Нажмите кью"
    assert latin.read("q") == "к"
    assert latin.read("SILVERHAND") == "Силверханд"


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        ("Сигнал | пропал _ ~", "Сигнал пропал"),
        ("Всё хорошо... наверное.", "Всё хорошо … наверное."),
        ("Ну.. ладно", "Ну … ладно"),
        ("Ой…", "Ой"),
        ("…Есть!", "Есть!"),
        ("Слово ° ещё  слово", "Слово ещё слово"),
        ("Привет , как дела ?", "Привет, как дела?"),
        ("Ст+ой — кто ид+ёт?", "Ст+ой — кто ид+ёт?"),
    ],
)
def test_clean(text: str, cleaned: str) -> None:
    assert clean(text) == cleaned


def test_up_to_two_sentences_stay_together() -> None:
    assert split_sentences("Привет. Как дела?") == ["Привет. Как дела?"]


def test_long_text_is_split() -> None:
    assert split_sentences("Раз. Два! Три? Четыре … пять.") == [
        "Раз.",
        "Два!",
        "Три?",
        "Четыре … пять.",
    ]


def test_empty() -> None:
    assert split_sentences("   ") == []
