from subvoicer.app.profile_commands import with_skip_phrase
from subvoicer.config.profile import Profile
from subvoicer.ui.line_window import stem, suggested_entry, words
from tests.unit.config.profiles import profile_data


def test_words_of_a_line() -> None:
    assert words("Арасака ещё хуже, чем Милитех, пацан. Код 4417!") == [
        "Арасака",
        "ещё",
        "хуже",
        "чем",
        "Милитех",
        "пацан",
        "Код",
    ]
    assert words("По-хорошему") == ["По-хорошему"]


def test_stem_for_all_forms() -> None:
    assert stem("Арасака") == "Арасак"
    assert stem("Милитех") == "Милитех"
    assert stem("Найт-Сити") == "Найт-Сит"
    assert stem("Такэмуре") == "Такэмур"
    assert stem("ещё") == "ещё"  # too short to cut


def test_suggested_entry() -> None:
    assert suggested_entry("Арасака", True) == ("Арасак*", "Арасак*")
    assert suggested_entry("Ви", False) == ("Ви", "Ви")


def test_a_skip_phrase_is_added_once() -> None:
    """«Не озвучивать такое» (TZ 1.33): the same phrase in other case or script is there."""
    profile = Profile.model_validate(profile_data(skip_phrases=["[Е] Взять"]))
    added = with_skip_phrase(profile, "  Влезть ")
    assert added is not None and added.skip_phrases == ["[Е] Взять", "Влезть"]
    assert with_skip_phrase(profile, "[E] взять") is None
    assert with_skip_phrase(profile, " … ") is None
