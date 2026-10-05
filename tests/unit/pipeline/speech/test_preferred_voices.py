"""Better voices first (TZ 1.19): profile defaults, character assignment, lists."""

from __future__ import annotations

from subvoicer.config.profile import Character
from subvoicer.config.resources import voice_catalog
from subvoicer.core.models import Gender, Voice
from subvoicer.engines.tts.silero import voice_id
from subvoicer.pipeline.speaker.characters import pick_voice
from subvoicer.pipeline.speech.voices import by_preference, default_voices

M, F = Gender.MALE, Gender.FEMALE
VOICES = [
    Voice("ru_alexandr", M, "Alexandr"),
    Voice("ru_bogdan", M, "Bogdan"),
    Voice("ru_aigul", F, "Aigul"),
    Voice("ru_albina", F, "Albina"),
    Voice("v55_aidar", M, "Aidar", preferred=True),
    Voice("v55_eugene", M, "Eugene", preferred=True),
    Voice("v55_baya", F, "Baya", preferred=True),
]


def character(name: str, gender: Gender, voice: str | None = None) -> Character:
    return Character(name=name, gender=gender.value, voice=voice)


def test_defaults_come_from_the_better_voices() -> None:
    defaults = default_voices(VOICES)
    assert (defaults.narrator, defaults.male, defaults.female) == (
        "v55_aidar",
        "v55_eugene",
        "v55_baya",
    )
    assert default_voices(VOICES[:4]).narrator == "ru_alexandr"  # without them: as before


def test_characters_get_better_voices_first_then_the_rest() -> None:
    cast: list[Character] = []
    for name in ("Джеки", "Такэмура", "Ривер"):
        new = character(name, M)
        cast.append(new.model_copy(update={"voice": pick_voice(new, cast, VOICES)}))
    voices = [c.voice for c in cast]
    assert set(voices[:2]) == {"v55_aidar", "v55_eugene"}
    assert voices[2] in {"ru_alexandr", "ru_bogdan"}
    # all taken: the start voice among the better ones
    many = [character(f"m{i}", M, v.id) for i, v in enumerate(VOICES) if v.gender is M]
    assert pick_voice(character("Декстер", M), many, VOICES) in {"v55_aidar", "v55_eugene"}


def test_lists_show_better_voices_first() -> None:
    assert [v.id for v in by_preference(VOICES)][:3] == ["v55_aidar", "v55_baya", "v55_eugene"]


def test_catalog_marks_the_v5_5_voices() -> None:
    catalog = voice_catalog("silero_v5")
    preferred = sorted(v.id for v in catalog.values() if v.preferred)
    assert preferred == ["v55_aidar", "v55_baya", "v55_eugene", "v55_kseniya", "v55_xenia"]
    assert len(catalog) == 34
    assert voice_id("v5_5_ru", "baya") == "v55_baya"
    assert voice_id("v5_cis_base", "ru_aigul") == "ru_aigul"
