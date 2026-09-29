import pytest

from subvoicer.config.resources import gender_exceptions
from subvoicer.core.models import Gender
from subvoicer.pipeline.speaker.gender import GenderGuesser


@pytest.fixture
def guesser() -> GenderGuesser:
    return GenderGuesser(gender_exceptions())


@pytest.mark.parametrize(
    ("text", "gender"),
    [
        ("Я пошла домой.", Gender.FEMALE),
        ("Вчера я вернулась поздно.", Gender.FEMALE),
        ("Устала я от этого.", Gender.FEMALE),
        ("Я рада тебя видеть!", Gender.FEMALE),
        ("я уверена", Gender.FEMALE),
        ("Я пошёл домой.", Gender.MALE),
        ("Я вернулся.", Gender.MALE),
        ("Сказал я ему всё.", Gender.MALE),
        ("Я рад, что ты здесь.", Gender.MALE),
        ("Я готов.", Gender.MALE),
        ("Я пол мыть не буду.", Gender.UNKNOWN),  # «пол» is a noun
        ("Я стол не двигаю.", Gender.UNKNOWN),
        ("Я орёл!", Gender.UNKNOWN),
        ("Мы пришли.", Gender.UNKNOWN),
        ("Я пошла, а я пошёл.", Gender.UNKNOWN),  # both
        ("Встретимся у моста.", Gender.UNKNOWN),
    ],
)
def test_gender(guesser: GenderGuesser, text: str, gender: Gender) -> None:
    assert guesser.guess(text) is gender


def test_exceptions_resource_matches_tz() -> None:
    assert len(gender_exceptions()) == 17
    assert "котёл" in gender_exceptions()
