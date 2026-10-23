import pytest

from subvoicer.config.characters_csv import parse_characters
from subvoicer.config.profile import Character
from subvoicer.config.resources import gender_exceptions
from subvoicer.core.models import Gender, Voice
from subvoicer.pipeline.speaker.characters import (
    CharacterBook,
    find_character,
    import_characters,
    merge_characters,
    pick_voice,
)
from subvoicer.pipeline.speaker.gender import GenderGuesser

VOICES = [
    Voice("m1", Gender.MALE, "m1"),
    Voice("m2", Gender.MALE, "m2"),
    Voice("m3", Gender.MALE, "m3"),
    Voice("f1", Gender.FEMALE, "f1"),
    Voice("f2", Gender.FEMALE, "f2"),
]
MALE = {"m1", "m2", "m3"}

JUDY = Character(name="Джуди Альварес", aliases=["Джуди"], gender=Gender.FEMALE)


@pytest.fixture
def book() -> CharacterBook:
    return CharacterBook(VOICES, GenderGuesser(gender_exceptions()))


@pytest.mark.parametrize(
    ("name", "found"),
    [
        ("Джуди", 0),
        ("ДЖУДИ!", 0),
        ("Джудн", 0),  # one OCR error in a 5-letter name
        ("Джудн Альварес", 0),
        ("Джун", None),  # two letters off
        ("Вн", None),  # short names: exact only
        ("Ви", 1),
        ("Бу", 1),  # look-alike letters of a small italic name (Cyberpunk letsplay)
        ("Ву", 1),
        ("БИ", 1),
        ("By", 1),  # Latin twins
        ("ДжудиАльварес", 0),  # a space lost
        ("Панан", 2),
        ("Панама", 2),
    ],
)
def test_find(name: str, found: int | None) -> None:
    characters = [JUDY, Character(name="Ви"), Character(name="Панам")]
    assert find_character(characters, name) == found


def test_latin_twins_of_a_short_name() -> None:
    """Cyberpunk letsplay: «TOm» for «ТОМ» made a second character with its own voice."""
    assert find_character([Character(name="ТОМ")], "TOm") == 0
    assert find_character([Character(name="Том")], "Tim") is None


def test_closest_wins() -> None:
    characters = [Character(name="Мира"), Character(name="Мина")]
    assert find_character(characters, "Мина") == 1
    assert find_character(characters, "Мила") == 0  # equal distance: the first


def test_voice_is_stable_and_unique() -> None:
    a = Character(name="Ривер", gender=Gender.MALE)
    first = pick_voice(a, [], VOICES)
    assert first in MALE
    assert pick_voice(a, [], VOICES) == first
    assert pick_voice(a, [Character(name="Другой", voice=first)], VOICES) != first
    everyone = [Character(name=f"x{i}", voice=f"m{i}") for i in (1, 2, 3)]
    assert pick_voice(a, everyone, VOICES) == first  # all taken -> the start one
    assert pick_voice(Character(name="Кто-то"), [], VOICES) in {v.id for v in VOICES}


def test_new_character_on_first_line(book: CharacterBook) -> None:
    r = book.resolve([], "Панам", "Я сама разберусь.")
    assert r.changed and r.character.source == "auto"
    assert r.character.gender is Gender.FEMALE and r.character.voice in {"f1", "f2"}
    again = book.resolve(r.characters, "Панан", "Поехали.")
    assert not again.changed and again.character == r.character and len(again.characters) == 1


def test_gender_refinement_reassigns_unlocked_voice(book: CharacterBook) -> None:
    r = book.resolve([], "Керри", "Поехали.")
    assert r.character.gender is Gender.UNKNOWN and r.character.voice is not None
    r2 = book.resolve(r.characters, "Керри", "Я пошёл.")
    assert r2.changed and r2.character.gender is Gender.MALE and r2.character.voice in MALE


def test_locked_voice_and_gender_are_kept(book: CharacterBook) -> None:
    locked = Character(name="Джонни", voice="f1", voice_locked=True)
    r = book.resolve([locked], "Джонни", "Я пошёл.")
    assert r.character.gender is Gender.MALE and r.character.voice == "f1"  # hand-picked voice
    pinned = Character(name="Такэмура", gender_locked=True, voice="m1")
    r = book.resolve([pinned], "Такэмура", "Я пошла.")
    assert not r.changed and r.character.gender is Gender.UNKNOWN


def test_merge() -> None:
    characters = [Character(name="Джудн", aliases=["Дж"]), JUDY.model_copy(update={"voice": "f2"})]
    merged = merge_characters(characters, "Джудн", "Джуди")
    assert len(merged) == 1
    assert merged[0].aliases == ["Джуди", "Джудн", "Дж"] and merged[0].voice == "f2"
    with pytest.raises(KeyError):
        merge_characters(characters, "Нет", "Джуди")
    with pytest.raises(ValueError):
        merge_characters(characters, "Джуди", "Джуди Альварес")


def test_import() -> None:
    existing = [
        JUDY.model_copy(update={"gender_locked": True, "voice": "f1", "voice_locked": True}),
        Character(name="Джонни", voice="m2"),
    ]
    rows, _ = parse_characters(
        "Джуди Альварес;м;Джуди|Джу\nДжонни Сильверхенд;м;Джонни|Сильверхенд\nТакэмура;;\nДжуди;;\n"
    )
    result, report = import_characters(existing, rows)
    judy, johnny, takemura = result
    assert judy.gender is Gender.FEMALE and judy.aliases == ["Джуди", "Джу"]  # gender locked
    assert judy.voice == "f1" and judy.voice_locked
    assert johnny.gender is Gender.MALE and johnny.gender_locked and johnny.voice == "m2"
    assert johnny.aliases == ["Джонни Сильверхенд", "Сильверхенд"]
    assert takemura.source == "import" and not takemura.gender_locked and takemura.voice is None
    assert report.added == ["Такэмура"]
    assert report.updated == ["Джуди Альварес", "Джонни"]
    assert [(p.line, p.reason) for p in report.skipped] == [(4, "уже есть, без изменений")]
