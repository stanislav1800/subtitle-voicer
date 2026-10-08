"""Profile examples for config tests."""

from __future__ import annotations

from typing import Any

from subvoicer.config.profile import SCHEMA_VERSION


def dialog_zone(**policy: Any) -> dict[str, Any]:
    return {
        "id": "dialog",
        "name": "Диалоги",
        "kind": "text",
        "rect": [0.2, 0.82, 0.8, 0.95],
        "trigger": "auto",
        "policy": {"priority": "high", "speaker": {"mode": "prefix"}, **policy},
        "mask": {"preset": "bright_on_dark"},
    }


def profile_data(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exe": "Game.exe",
        "install_dir": None,
        "title": "Игра",
        "layouts": {"16:9": {"zones": [dialog_zone()]}},
        "narrator_voice": "n",
        "default_voices": {"male": "m", "female": "f"},
    }
    data.update(fields)
    return data
