"""Parsing the character import text (TZ 4.6): ``имя;пол;варианты``, UTF-8, header optional.

Only parsing lives here; matching rows to the profile's characters is
``pipeline/speaker/characters.py`` (it needs the name matching rules of 4.5).
"""

from __future__ import annotations

from dataclasses import dataclass

from subvoicer.core.models import Gender

GENDERS = {"м": Gender.MALE, "ж": Gender.FEMALE, "": None}
HEADER_FIRST_CELL = "имя"


@dataclass(frozen=True, slots=True)
class CsvCharacter:
    line: int  # 1-based line number, for the report
    name: str
    gender: Gender | None  # None: not given in the file
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CsvProblem:
    line: int
    text: str
    reason: str


def parse_characters(text: str) -> tuple[list[CsvCharacter], list[CsvProblem]]:
    rows: list[CsvCharacter] = []
    problems: list[CsvProblem] = []
    for number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        cells = [cell.strip() for cell in line.split(";")]
        if number == 1 and cells[0].lower() == HEADER_FIRST_CELL:
            continue
        if len(cells) > 3:
            problems.append(CsvProblem(number, line, "больше трёх полей"))
            continue
        cells += [""] * (3 - len(cells))
        name, gender_cell, aliases_cell = cells
        if not name:
            problems.append(CsvProblem(number, line, "пустое имя"))
            continue
        gender_key = gender_cell.lower()
        if gender_key not in GENDERS:
            problems.append(CsvProblem(number, line, f"пол «{gender_cell}»: нужно м, ж или пусто"))
            continue
        aliases = tuple(a.strip() for a in aliases_cell.split("|") if a.strip())
        rows.append(CsvCharacter(number, name, GENDERS[gender_key], aliases))
    return rows, problems
