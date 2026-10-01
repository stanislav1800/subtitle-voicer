from subvoicer.config.characters_csv import parse_characters
from subvoicer.core.models import Gender

TZ_EXAMPLE = """имя;пол;варианты
Джуди Альварес;ж;Джуди
Джонни Сильверхенд;м;Джонни|Сильверхенд
Такэмура;;
"""


def test_parse_tz_example() -> None:
    rows, problems = parse_characters(TZ_EXAMPLE)
    assert problems == []
    assert [(r.name, r.gender, r.aliases) for r in rows] == [
        ("Джуди Альварес", Gender.FEMALE, ("Джуди",)),
        ("Джонни Сильверхенд", Gender.MALE, ("Джонни", "Сильверхенд")),
        ("Такэмура", None, ()),
    ]


def test_without_header_and_problems() -> None:
    rows, problems = parse_characters("﻿Панам;Ж\n;м;x\nКерри;x\nа;б;в;г\n\nРивер\n")
    assert [r.name for r in rows] == ["Панам", "Ривер"]
    assert rows[0].gender is Gender.FEMALE
    assert [(p.line, p.reason) for p in problems] == [
        (2, "пустое имя"),
        (3, "пол «x»: нужно м, ж или пусто"),
        (4, "больше трёх полей"),
    ]
