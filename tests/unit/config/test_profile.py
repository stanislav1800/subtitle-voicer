import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from subvoicer.config.profile import Profile
from subvoicer.config.settings import Settings, load_settings
from tests.unit.config.profiles import dialog_zone, profile_data

TZ_EXAMPLE: dict[str, Any] = {  # TZ 5.4, voices replaced
    "schema_version": 3,
    "exe": "Cyberpunk2077.exe",
    "install_dir": "D:\\Games\\Steam\\steamapps\\common\\Cyberpunk 2077",
    "title": "Cyberpunk 2077",
    "icon": "icons/cyberpunk2077.png",
    "layout": {
        "zones": [
            {
                "id": "dialog",
                "name": "Диалоги",
                "kind": "text",
                "rect": [0.20, 0.82, 0.80, 0.95],
                "trigger": "auto",
                "policy": {
                    "priority": "high",
                    "voice": {"mode": "speaker"},
                    "speaker": {
                        "mode": "color",
                        "colors_hsv": [[[0, 100, 150], [15, 255, 255]]],
                    },
                    "repeat_cooldown_min": 0,
                    "duck": True,
                },
                "mask": {"preset": "color", "colors_hsv": [[[80, 80, 150], [100, 255, 255]]]},
            },
            {
                "id": "note",
                "name": "Записка",
                "kind": "text",
                "rect": [0.30, 0.15, 0.70, 0.80],
                "trigger": "hotkey",
                "hotkey": "<ctrl>+<alt>+1",
                "policy": {
                    "voice": {"mode": "narrator"},
                    "speaker": {"mode": "none"},
                    "duck": True,
                },
                "mask": {"preset": "adaptive"},
            },
            {
                "id": "objective",
                "name": "Цель задания",
                "kind": "text",
                "rect": [0.78, 0.18, 0.98, 0.26],
                "trigger": "auto",
                "policy": {
                    "priority": "low",
                    "voice": {"mode": "fixed", "id": "v1"},
                    "speaker": {"mode": "none"},
                    "repeat_cooldown_min": 10,
                    "duck": False,
                },
                "mask": {"preset": "bright_on_dark", "threshold": 200},
                "stabilization": {"stable_ms": 400},
            },
        ]
    },
    "narrator_voice": "v1",
    "default_voices": {"male": "v1", "female": "v2"},
    "speaker_stoplist": ["совет"],
    "characters": [
        {
            "name": "Джуди Альварес",
            "aliases": ["Джуди"],
            "gender": "female",
            "gender_locked": True,
            "voice": "v2",
            "voice_locked": True,
            "source": "import",
        },
    ],
    "pronunciation": [
        {"from": "Ви", "to": "В+и"},
        {"from": "\\bNCPD\\b", "to": "эн си пи ди", "regex": True},
    ],
    "ocr": {"engine": "rapidocr", "min_confidence": 0.6, "upscale": 2.0},
    "stabilization": {
        "stable_ms": 200,
        "max_wait_ms": 1500,
        "match_ratio": 90,
        "change_ratio": 0.005,
        "empty_ratio": 0.001,
    },
    "hooks": None,
}


def test_tz_example_round_trips() -> None:
    profile = Profile.model_validate(TZ_EXAMPLE)
    again = json.loads(profile.to_json())
    assert again["pronunciation"][0] == {"from": "Ви", "to": "В+и", "regex": False}
    assert Profile.model_validate(again) == profile
    zone = profile.layout.zones[2]
    assert profile.stabilization.merged(zone.stabilization).stable_ms == 400
    assert profile.stabilization.merged(zone.stabilization).max_wait_ms == 1500


def _zones(*zones: dict[str, Any]) -> dict[str, Any]:
    return {"zones": list(zones)}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"surprise": 1}, "surprise"),
        ({"layout": _zones(dialog_zone(priority="urgent"))}, "priority"),
        ({"layout": _zones(dialog_zone(), dialog_zone())}, "Повторяющиеся"),
        (
            {"layout": _zones(dialog_zone(speaker={"mode": "zone", "zone_id": "x"}))},
            "zone_id",
        ),
        ({"exe": None, "install_dir": None}, "exe или install_dir"),
    ],
)
def test_invalid_profiles_are_errors(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Profile.model_validate(profile_data(**change))


def test_zone_rules() -> None:
    zone = dialog_zone()
    zone["rect"] = [0.5, 0.5, 0.4, 0.9]
    with pytest.raises(ValidationError, match="пустой"):
        Profile.model_validate(profile_data(layout=_zones(zone)))
    speaker = {
        "id": "name",
        "kind": "speaker",
        "rect": [0, 0, 0.5, 0.1],
        "mask": {"preset": "bright_on_dark"},
        "trigger": "auto",
    }
    with pytest.raises(ValidationError, match="speaker"):
        Profile.model_validate(profile_data(layout=_zones(speaker)))
    hotkey = dialog_zone()
    hotkey["trigger"] = "hotkey"
    with pytest.raises(ValidationError, match="hotkey"):
        Profile.model_validate(profile_data(layout=_zones(hotkey)))
    color = dialog_zone()
    color["mask"] = {"preset": "color", "colors_hsv": [[[0, 0, 0], [200, 0, 0]]]}
    with pytest.raises(ValidationError, match="HSV"):
        Profile.model_validate(profile_data(layout=_zones(color)))


def test_settings_defaults_and_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings = load_settings(path)
    assert settings.overlap_mode == "speedup"
    assert "firefox.exe" in settings.excluded_exes
    assert settings.hotkeys["pause"] == "<ctrl>+<alt>+p"
    path.write_text('base_rate = 1.3\nnever_ask_exes = ["a.exe"]\n', encoding="utf-8")
    assert load_settings(path).base_rate == 1.3
    path.write_text("speed = 2\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="speed"):
        load_settings(path)
    assert Settings().duck_level == 0.4


def test_settings_round_trip_keeps_comments(tmp_path: Path) -> None:
    from subvoicer.config.settings import save_settings

    path = tmp_path / "settings.toml"
    path.write_text("# моя скорость\nbase_rate = 1.3\n", encoding="utf-8")
    settings = load_settings(path).model_copy(update={"duck_level": 0.25})
    save_settings(path, settings)
    text = path.read_text(encoding="utf-8")
    assert "# моя скорость" in text
    again = load_settings(path)
    assert (again.base_rate, again.duck_level) == (1.3, 0.25)
    assert again == settings
