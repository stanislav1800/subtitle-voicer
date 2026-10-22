"""Choosing a voice for a line (TZ 4.6). Characters and their own voices come with stage 5;
until then a named speaker is voiced like an unnamed one."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from subvoicer.core.models import Gender, Voice
from subvoicer.pipeline.speaker.gender import GenderGuesser


class VoiceMode(StrEnum):
    SPEAKER = "speaker"  # by who speaks: character voice / gender default / narrator
    GENDER = "gender"  # by gender, the profile voices only (TZ 1.32)
    FIXED = "fixed"
    NARRATOR = "narrator"


@dataclass(frozen=True, slots=True)
class VoiceDefaults:
    narrator: str
    male: str
    female: str


def by_preference(voices: Sequence[Voice]) -> list[Voice]:
    """Better voices first (``preferred``, TZ 1.19), then character voices (stage 16), then the
    rest; by id within each group."""
    return sorted(voices, key=lambda v: (not v.preferred, not v.character, v.id))


def automatic(voices: Sequence[Voice]) -> list[Voice]:
    """What the automatic assignment may pick (TZ 4.6): character voices only by hand (1.21)."""
    return [v for v in voices if not v.character]


def default_voices(voices: Sequence[Voice]) -> VoiceDefaults:
    """Profile defaults (TZ 4.6, 1.19): narrator — first male, male — second male (or the
    first), female — first female, better voices first; the first voice of the catalog if a
    gender is missing."""
    ordered = by_preference(automatic(voices))
    if not ordered:
        raise ValueError("Нет ни одного голоса")
    males = [v.id for v in ordered if v.gender is Gender.MALE]
    females = [v.id for v in ordered if v.gender is Gender.FEMALE]
    first = ordered[0].id
    return VoiceDefaults(
        narrator=males[0] if males else first,
        male=males[1] if len(males) > 1 else (males[0] if males else first),
        female=females[0] if females else first,
    )


class VoicePicker:
    def __init__(self, defaults: VoiceDefaults, gender: GenderGuesser) -> None:
        self.defaults = defaults
        self._gender = gender

    def pick(self, mode: VoiceMode, text: str, fixed_id: str | None = None) -> str:
        match mode:
            case VoiceMode.FIXED:
                if not fixed_id:
                    raise ValueError("Для голоса fixed нужен id голоса")
                return fixed_id
            case VoiceMode.NARRATOR:
                return self.defaults.narrator
            case VoiceMode.SPEAKER | VoiceMode.GENDER:
                return self.by_gender(self._gender.guess(text))

    def by_gender(self, gender: Gender) -> str:
        """The profile voice of a gender; unknown — the narrator."""
        if gender is Gender.MALE:
            return self.defaults.male
        if gender is Gender.FEMALE:
            return self.defaults.female
        return self.defaults.narrator
