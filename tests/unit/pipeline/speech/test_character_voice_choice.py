"""Character voices (stage 16, TZ 1.21) are assigned only by hand: never by the automatics."""

from __future__ import annotations

from subvoicer.config.profile import Character
from subvoicer.config.resources import character_voices, voice_catalog
from subvoicer.core.models import Gender, Voice
from subvoicer.pipeline.speaker.characters import pick_voice
from subvoicer.pipeline.speech.voices import by_preference, default_voices

M, F = Gender.MALE, Gender.FEMALE
VOICES = [
    Voice("char_geralt", M, "Геральт", character=True),
    Voice("char_vi", F, "Ви", character=True),
    Voice("ru_alexandr", M, "Alexandr"),
    Voice("v55_eugene", M, "Eugene", preferred=True),
    Voice("v55_xenia", F, "Xenia", preferred=True),
]


def test_new_characters_never_get_a_character_voice() -> None:
    cast: list[Character] = []
    for name, gender in (("Джеки", M), ("Такэмура", M), ("Джуди", F), ("Панам", F)):
        new = Character(name=name, gender=gender.value)
        cast.append(new.model_copy(update={"voice": pick_voice(new, cast, VOICES)}))
    assert not {c.voice for c in cast} & {"char_geralt", "char_vi"}
    unknown = Character(name="Кто-то", gender="unknown")
    assert pick_voice(unknown, [], VOICES) not in {"char_geralt", "char_vi"}


def test_profile_defaults_skip_character_voices() -> None:
    defaults = default_voices(VOICES)
    assert (defaults.narrator, defaults.male, defaults.female) == (
        "v55_eugene",
        "ru_alexandr",
        "v55_xenia",
    )


def test_lists_show_better_voices_then_character_voices_then_the_rest() -> None:
    assert [v.id for v in by_preference(VOICES)] == [
        "v55_eugene",
        "v55_xenia",
        "char_geralt",
        "char_vi",
        "ru_alexandr",
    ]


def test_the_four_chosen_voices_sit_on_better_silero_voices() -> None:
    catalog = voice_catalog("silero_v5")
    voices = {v.name: v for v in character_voices()}
    assert set(voices) == {"Ви", "Джонни", "Цири", "Геральт"}
    assert all(catalog[v.base].preferred for v in voices.values())
    assert voices["Ви"].base == "v55_xenia" and voices["Ви"].kind == "rvc"
    assert voices["Цири"].base == "v55_xenia" and voices["Цири"].kind == "svc"
    assert {voices["Геральт"].base, voices["Джонни"].base} == {"v55_eugene"}
    assert dict(voices["Ви"].params) == {"key": -3.0, "index": 1.0, "protect": 0.5, "rms": 1.0}
    assert dict(voices["Геральт"].params)["f0"] == "dio"
    assert len({v.id for v in voices.values()} & set(catalog)) == 0
