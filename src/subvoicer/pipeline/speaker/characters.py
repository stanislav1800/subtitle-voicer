"""Characters of a profile (TZ 4.5, 4.6): matching a name, voices, gender, merge, import.

Works on ``Character`` models and returns new lists; saving is the caller's job.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from rapidfuzz.distance import Levenshtein

from subvoicer.config.characters_csv import CsvCharacter, CsvProblem
from subvoicer.config.profile import Character
from subvoicer.core.models import Gender, Voice
from subvoicer.pipeline.normalize import normalize
from subvoicer.pipeline.speaker.gender import GenderGuesser


def allowed_errors(name: str) -> int:
    """Letters an OCR error may change (TZ 1.16): 1 for 4-9 letters, 2 from 10, else 0."""
    letters = len(name.replace(" ", ""))
    if letters >= 10:
        return 2
    if letters >= 4:
        return 1
    return 0


def names_of(character: Character) -> list[str]:
    return [character.name, *character.aliases]


def find_character(characters: Sequence[Character], name: str) -> int | None:
    """Index of the character called ``name``: exact (normalised) first, then the closest
    name or alias within its allowed number of letter errors."""
    wanted = normalize(name)
    if not wanted:
        return None
    for index, character in enumerate(characters):
        if any(normalize(n) == wanted for n in names_of(character)):
            return index
    best: tuple[int, int] | None = None  # (distance, index)
    for index, character in enumerate(characters):
        for known in names_of(character):
            norm = normalize(known)
            limit = allowed_errors(norm)
            if limit == 0:
                continue
            distance = Levenshtein.distance(wanted, norm, score_cutoff=limit)
            if distance <= limit and (best is None or distance < best[0]):
                best = (distance, index)
    return None if best is None else best[1]


def pick_voice(
    character: Character, characters: Sequence[Character], voices: Sequence[Voice]
) -> str | None:
    """TZ 4.6, 1.19: voices of the character's gender (all for unknown) sorted by id, start at
    sha1(name) % n, skip voices other characters have — first among the better voices
    (``preferred``), then among the rest; all taken -> the start one of the better voices."""
    if character.gender is Gender.UNKNOWN:
        pool = list(voices)
    else:
        pool = [v for v in voices if v.gender is character.gender] or list(voices)
    if not pool:
        return None
    digest = int(hashlib.sha1(character.name.lower().encode("utf-8")).hexdigest(), 16)
    taken = {c.voice for c in characters if c.voice and c.name != character.name}
    tiers = [
        sorted(v.id for v in pool if v.preferred),
        sorted(v.id for v in pool if not v.preferred),
    ]
    tiers = [ids for ids in tiers if ids]
    for ids in tiers:
        start = digest % len(ids)
        for step in range(len(ids)):
            candidate = ids[(start + step) % len(ids)]
            if candidate not in taken:
                return candidate
    first = tiers[0]
    return first[digest % len(first)]


@dataclass(frozen=True, slots=True)
class Resolved:
    character: Character
    characters: list[Character]  # the updated list
    changed: bool


class CharacterBook:
    """A named speaker -> a character with a voice; new characters appear on their first
    voiced line, an unknown gender is refined by the line's text."""

    def __init__(self, voices: Sequence[Voice], gender: GenderGuesser) -> None:
        self._voices = list(voices)
        self._gender = gender

    def resolve(self, characters: Sequence[Character], name: str, text: str) -> Resolved:
        result = list(characters)
        guessed = self._gender.guess(text)
        index = find_character(result, name)
        changed = False
        if index is None:
            character = Character(name=name.strip(), gender=guessed, source="auto")
            result.append(character)
            index, changed = len(result) - 1, True
        character = result[index]
        if (
            not character.gender_locked
            and character.gender is Gender.UNKNOWN
            and guessed is not Gender.UNKNOWN
        ):
            update: dict[str, object] = {"gender": guessed}
            if not character.voice_locked:
                update["voice"] = None  # reassigned below from the voices of that gender
            character = character.model_copy(update=update)
            changed = True
        if character.voice is None:
            voice = pick_voice(character, result, self._voices)
            if voice is not None:
                character = character.model_copy(update={"voice": voice})
                changed = True
        result[index] = character
        return Resolved(character, result, changed)


def merge_characters(characters: Sequence[Character], source: str, target: str) -> list[Character]:
    """A becomes a variant of B: A's name and aliases join B's aliases, A is removed; B keeps
    its voice and gender (TZ 4.6)."""
    a, b = find_exact(characters, source), find_exact(characters, target)
    if a is None:
        raise KeyError(source)
    if b is None:
        raise KeyError(target)
    if a == b:
        raise ValueError("Нельзя объединить персонажа с самим собой")
    known = {normalize(n) for n in names_of(characters[b])}
    aliases = list(characters[b].aliases)
    for name in names_of(characters[a]):
        if normalize(name) not in known:
            aliases.append(name)
            known.add(normalize(name))
    merged = characters[b].model_copy(update={"aliases": aliases})
    return [merged if i == b else c for i, c in enumerate(characters) if i != a]


def find_exact(characters: Sequence[Character], name: str) -> int | None:
    wanted = normalize(name)
    for index, character in enumerate(characters):
        if any(normalize(n) == wanted for n in names_of(character)):
            return index
    return None


@dataclass
class ImportReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[CsvProblem] = field(default_factory=list)


def import_characters(
    characters: Sequence[Character], rows: Sequence[CsvCharacter]
) -> tuple[list[Character], ImportReport]:
    """TZ 4.6: found by name or variants (4.5) -> add missing variants, set the gender only
    if not locked and given; new -> ``source = import``. Voices are never touched."""
    result = list(characters)
    report = ImportReport()
    for row in rows:
        index = None
        for name in (row.name, *row.aliases):
            index = find_character(result, name)
            if index is not None:
                break
        if index is None:
            result.append(
                Character(
                    name=row.name,
                    aliases=list(_unique(row.aliases, exclude=[row.name])),
                    gender=row.gender or Gender.UNKNOWN,
                    gender_locked=row.gender is not None,
                    source="import",
                )
            )
            report.added.append(row.name)
            continue
        character = result[index]
        update: dict[str, object] = {}
        # a name the character already answers to, even with an OCR-like typo, adds nothing
        fresh = [n for n in (row.name, *row.aliases) if find_character([character], n) is None]
        new_aliases = _unique(fresh, exclude=names_of(character), keep=character.aliases)
        if new_aliases != character.aliases:
            update["aliases"] = new_aliases
        if (
            row.gender is not None
            and not character.gender_locked
            and character.gender is not row.gender
        ):
            update["gender"] = row.gender
            update["gender_locked"] = True
        if update:
            result[index] = character.model_copy(update=update)
            report.updated.append(character.name)
        else:
            report.skipped.append(CsvProblem(row.line, row.name, "уже есть, без изменений"))
    return result, report


def _unique(names: Sequence[str], exclude: Sequence[str], keep: Sequence[str] = ()) -> list[str]:
    """``keep`` followed by the names not yet known (normalised), in order."""
    seen = {normalize(n) for n in (*exclude, *keep)}
    out = list(keep)
    for name in names:
        norm = normalize(name)
        if norm and norm not in seen:
            out.append(name.strip())
            seen.add(norm)
    return out
