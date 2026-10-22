"""General settings ``settings.toml`` (TZ 4.14): only the keys of the table there."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import Field

from subvoicer.config.migrations import Migration, ProfileTooNewError, migrate
from subvoicer.config.profile import OverlapName, StrictModel
from subvoicer.config.resources import read_list

DEFAULT_HOTKEYS = {  # TZ 4.12
    "pause": "<ctrl>+<alt>+p",
    "repeat": "<ctrl>+<alt>+r",
    "skip": "<ctrl>+<alt>+s",
    "select_read": "<ctrl>+<alt>+a",
    "zone_editor": "<ctrl>+<alt>+z",
    "debug_capture": "<ctrl>+<alt>+d",
}


SETTINGS_SCHEMA_VERSION = 3  # 1: before stage 9, without the key (TZ 1.18); 3: TZ 1.21


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("library_hidden", [])
    return data


def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("character_voices", False)
    return data


# from version N to N+1, each with a unit test on an old file
SETTINGS_MIGRATIONS: dict[int, Migration] = {1: _v1_to_v2, 2: _v2_to_v3}


def default_excluded_exes() -> list[str]:
    return read_list("excluded_exes.txt")


class Settings(StrictModel):
    schema_version: int = SETTINGS_SCHEMA_VERSION
    enabled: bool = True
    poll_hz: float = Field(default=10, gt=0, le=60)
    output_device: str = ""
    voice_volume: float = Field(default=1.0, ge=0, le=1)
    base_rate: float = Field(default=1.0, gt=0)
    overlap_mode: OverlapName = "speedup"
    hurry_rate: float = Field(default=1.5, ge=1)
    max_lag_s: float = Field(default=8, gt=0)
    duck_level: float = Field(default=0.4, ge=0, le=1)
    duck_fade_ms: int = Field(default=150, ge=0)
    duck_release_ms: int = Field(default=300, ge=0)
    low_priority_ttl_s: float = Field(default=5, ge=0)
    autostart: bool = False
    dev_mode: bool = False
    debug_overlay: bool = False
    history_size: int = Field(default=50, ge=1)
    tts_cache_size: int = Field(default=200, ge=0)
    excluded_exes: list[str] = Field(default_factory=default_excluded_exes)
    never_ask_exes: list[str] = Field(default_factory=list)
    library_sources: list[str] = Field(default_factory=lambda: ["steam", "epic", "gog"])
    library_hidden: list[str] = Field(default_factory=list)  # install dirs (TZ 1.18)
    character_voices: bool = False  # GPU character voices, if installed (stage 16, TZ 1.21)
    hotkeys: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_HOTKEYS))


def load_settings(path: Path) -> Settings:
    """Defaults when the file does not exist yet; a bad file is an error with the key names."""
    if not path.exists():
        return Settings()
    return Settings.model_validate(
        migrate_settings(tomllib.loads(path.read_text(encoding="utf-8")))
    )


def migrate_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Old files up to SETTINGS_SCHEMA_VERSION; a file without the key is version 1."""
    data = {"schema_version": 1, **data}
    try:
        return migrate(data, SETTINGS_SCHEMA_VERSION, SETTINGS_MIGRATIONS)
    except ProfileTooNewError as exc:
        raise ValueError(
            f"settings.toml создан более новой версией приложения "
            f"(формат {data['schema_version']}, известен до {SETTINGS_SCHEMA_VERSION})"
        ) from exc


def save_settings(path: Path, settings: Settings) -> None:
    """Atomic write; keys of the file that the user commented stay as tomlkit keeps them."""
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        document = None
    if document is None:
        document = tomlkit.document()
        document.add(tomlkit.comment("Subtitle Voicer — общие настройки (ТЗ 4.14)"))
    for key, value in settings.model_dump(mode="json").items():
        document[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(tomlkit.dumps(document), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
