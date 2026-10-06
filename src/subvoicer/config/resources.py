"""Loading default data files from the ``subvoicer.resources`` package."""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

from subvoicer.core.models import CharacterVoice, Gender, Voice

RESOURCES = "subvoicer.resources"


def resource_path(name: str) -> Path:
    return Path(str(files(RESOURCES).joinpath(name)))


def read_text(name: str) -> str:
    return files(RESOURCES).joinpath(name).read_text(encoding="utf-8")


def read_list(name: str) -> list[str]:
    """Non-empty lines of a list file; ``#`` starts a comment line."""
    lines = (line.strip() for line in read_text(name).splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def read_toml(name: str) -> dict[str, Any]:
    return tomllib.loads(read_text(name))


def short_words() -> list[str]:
    return read_list("short_words.txt")


def speaker_stoplist() -> list[str]:
    return read_list("speaker_stoplist.txt")


def gender_exceptions() -> list[str]:
    return read_list("gender_exceptions.txt")


def letter_names() -> dict[str, str]:
    return {k.lower(): str(v) for k, v in read_toml("letter_names.toml")["letters"].items()}


def translit_table() -> dict[str, str]:
    return {k.lower(): str(v) for k, v in read_toml("translit.toml")["translit"].items()}


def voice_catalog(engine: str) -> dict[str, Voice]:
    """``resources/voices/<engine>.toml``: gender and display name per voice id (TZ 4.6)."""
    try:
        data = read_toml(f"voices/{engine}.toml")
    except FileNotFoundError:
        return {}
    catalog = {}
    for item in data.get("voice", []):
        gender = Gender(item.get("gender", "unknown"))
        catalog[item["id"]] = Voice(
            item["id"], gender, item.get("name", item["id"]), bool(item.get("preferred", False))
        )
    return catalog


CHARACTER_KINDS = ("rvc", "svc")


def character_voices() -> list[CharacterVoice]:
    """``resources/voices/characters.toml``: character voices (stage 16, TZ 1.21)."""
    voices = []
    for item in read_toml("voices/characters.toml").get("voice", []):
        if item["kind"] not in CHARACTER_KINDS:
            raise ValueError(f"Голос {item['id']}: неизвестный тип модели {item['kind']!r}")
        params = tuple(
            (str(k), v if isinstance(v, str) else float(v))
            for k, v in item.get("params", {}).items()
        )
        voices.append(
            CharacterVoice(
                id=item["id"],
                name=item["name"],
                gender=Gender(item.get("gender", "unknown")),
                base=item["base"],
                kind=item["kind"],
                model=item["model"],
                params=params,
                gain_db=float(item.get("gain_db", 0.0)),
            )
        )
    return voices
