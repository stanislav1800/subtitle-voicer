"""settings.toml format versions (TZ 1.18): old files migrate, newer ones are refused."""

from __future__ import annotations

from pathlib import Path

import pytest

from subvoicer.config.settings import (
    SETTINGS_SCHEMA_VERSION,
    Settings,
    load_settings,
    save_settings,
)

V1 = """# Subtitle Voicer — общие настройки
enabled = false
autostart = true
library_sources = ["steam"]
"""


def test_v1_file_without_version_migrates(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(V1, encoding="utf-8")
    settings = load_settings(path)
    assert settings.schema_version == SETTINGS_SCHEMA_VERSION == 2
    assert settings.library_hidden == [] and settings.autostart and not settings.enabled
    save_settings(path, settings)
    text = path.read_text(encoding="utf-8")
    assert "schema_version = 2" in text and text.startswith("# Subtitle Voicer")


def test_newer_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("schema_version = 99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="более новой версией"):
        load_settings(path)


def test_defaults() -> None:
    assert Settings().library_hidden == []
