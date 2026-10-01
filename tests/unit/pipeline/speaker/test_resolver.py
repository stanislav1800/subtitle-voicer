import pytest

from subvoicer.config.resources import speaker_stoplist
from subvoicer.pipeline.speaker.extract import extract_prefix
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.speaker.validate import NameValidator


@pytest.fixture
def prefix() -> SpeakerResolver:
    return SpeakerResolver(SpeakerMode.PREFIX, speaker_stoplist())


def test_prefix_name_is_cut(prefix: SpeakerResolver) -> None:
    result = prefix.resolve("Джуди: Ты вовремя. Заходи.")
    assert result.name == "Джуди"
    assert result.text == "Ты вовремя. Заходи."


def test_prefix_two_word_name(prefix: SpeakerResolver) -> None:
    assert prefix.resolve("Джонни Сильверхенд: Ну давай!").name == "Джонни Сильверхенд"


def test_stoplisted_prefix_stays_in_text(prefix: SpeakerResolver) -> None:
    result = prefix.resolve("Совет: нажмите Q, чтобы уклониться.")
    assert result.name is None
    assert result.text == "Совет: нажмите Q, чтобы уклониться."


@pytest.mark.parametrize(
    "line",
    [
        "Встретимся в 22:30.",  # a time, not a name: digits
        "Встретимся на старом складе в 22:30.",  # more than 30 chars before the colon
        "джуди: без заглавной",  # lower case
        "А: одна буква",  # too short
        "Раз два три четыре: слишком много слов",
        "Без двоеточия вообще",
    ],
)
def test_not_a_name(prefix: SpeakerResolver, line: str) -> None:
    result = prefix.resolve(line)
    assert result.name is None
    assert result.text == line


def test_prefix_without_text_is_not_a_name(prefix: SpeakerResolver) -> None:
    assert prefix.resolve("Джуди:").name is None


def test_none_mode_keeps_everything() -> None:
    resolver = SpeakerResolver(SpeakerMode.NONE, speaker_stoplist())
    result = resolver.resolve("Джуди: Привет")
    assert result.name is None
    assert result.text == "Джуди: Привет"


def test_candidate_modes_validate_external_candidate() -> None:
    resolver = SpeakerResolver(SpeakerMode.ZONE, speaker_stoplist())
    assert resolver.resolve("Привет", candidate=" Панам ").name == "Панам"
    assert resolver.resolve("Привет", candidate="задание").name is None


def test_prefix_on_first_line_only() -> None:
    match = extract_prefix("Первая строка\nВторая: строка")
    assert match is None


def test_validator_rules() -> None:
    validator = NameValidator(["внимание"])
    assert validator.is_name("Ви")
    assert not validator.is_name("Внимание")
    assert not validator.is_name("ВНИМАНИЕ")
    assert not validator.is_name("x" * 31)
    assert not validator.is_name("Этап 2")


def test_stoplist_resource_matches_tz() -> None:
    assert set(speaker_stoplist()) == {
        "совет",
        "задание",
        "цель",
        "внимание",
        "подсказка",
        "примечание",
        "награда",
        "получено",
        "новое",
    }


def test_several_speakers_in_one_subtitle() -> None:
    r = SpeakerResolver(SpeakerMode.PREFIX, speaker_stoplist())
    found = r.resolve_all("Ви: А ты ещё кто? Джеки: Вылезай. Ви: Эй, спокойно.")
    assert [(x.name, x.text) for x in found] == [
        ("Ви", "А ты ещё кто?"),
        ("Джеки", "Вылезай."),
        ("Ви", "Эй, спокойно."),
    ]


def test_inner_names_need_a_named_line_and_a_sentence_end() -> None:
    r = SpeakerResolver(SpeakerMode.PREFIX, speaker_stoplist())
    plain = r.resolve_all("Он вышел. Джеки: так он и сказал.")  # no name at the start
    assert [(x.name, x.text) for x in plain] == [(None, "Он вышел. Джеки: так он и сказал.")]
    mid = r.resolve_all("Ви: Встретимся в 22: 30. Совет: не опаздывай.")
    assert [(x.name, x.text) for x in mid] == [("Ви", "Встретимся в 22: 30. Совет: не опаздывай.")]


def test_continuation_split() -> None:
    r = SpeakerResolver(SpeakerMode.PREFIX, speaker_stoplist())
    assert r.split_inner("и уходи. Джеки: Стой!") == [(None, "и уходи."), ("Джеки", "Стой!")]
    assert r.split_inner("Джеки: Стой!") == [(None, "Джеки: Стой!")]  # no sentence end before
    none = SpeakerResolver(SpeakerMode.NONE, speaker_stoplist())
    assert none.split_inner("Раз. Джеки: Стой!") == [(None, "Раз. Джеки: Стой!")]
