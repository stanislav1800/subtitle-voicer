from pathlib import Path

import pytest

from subvoicer.app import profile_commands as pc
from subvoicer.config.profile_store import ProfileStore
from subvoicer.core.models import Gender


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(tmp_path / "profiles")


def test_new_profile_has_tz_defaults(store: ProfileStore) -> None:
    stored = pc.new_profile(store, "Cyberpunk2077.exe", "Cyberpunk 2077")
    p = stored.profile
    assert stored.path.name == "cyberpunk2077.exe.json"
    assert p.narrator_voice and p.default_voices.male and p.default_voices.female
    assert "совет" in p.speaker_stoplist and p.layout.zones == []
    with pytest.raises(FileExistsError):
        pc.new_profile(store, "CYBERPUNK2077.EXE")


def test_add_zones_from_templates(store: ProfileStore) -> None:
    pc.new_profile(store, "game.exe")
    _, dialog = pc.add_zone(
        store,
        "game.exe",
        template="dialog",
        rect=(0.2, 0.9, 0.8, 0.99),
        overrides={"mask": {"preset": "color", "colors_hsv": [((0, 0, 190), (179, 60, 255))]}},
    )
    stored, objective = pc.add_zone(
        store,
        "game",
        template="objective",
        rect=(0.78, 0.18, 0.98, 0.26),
        overrides={"policy": {"repeat_cooldown_min": 5}},
    )
    assert [z.id for z in stored.profile.layout.zones] == ["dialog", "objective"]
    assert dialog.policy is not None and dialog.policy.priority == "high"
    assert dialog.mask.preset == "color"
    assert objective.policy is not None
    assert (objective.policy.priority, objective.policy.repeat_cooldown_min) == ("low", 5)
    assert objective.policy.duck is False
    _, second = pc.add_zone(store, "game.exe", template="dialog", rect=(0, 0, 1, 1))
    assert second.id == "dialog2"
    with pytest.raises(KeyError):
        pc.add_zone(store, "game.exe", template="nope", rect=(0, 0, 1, 1))


def test_find_profile(store: ProfileStore) -> None:
    pc.new_profile(store, "game.exe")
    assert pc.find_profile(store, "GAME.EXE").path.name == "game.exe.json"
    assert pc.find_profile(store, "game.exe.json").path.name == "game.exe.json"
    with pytest.raises(pc.ProfileNotFoundError):
        pc.find_profile(store, "other.exe")


def test_characters_import_and_merge(store: ProfileStore, tmp_path: Path) -> None:
    pc.new_profile(store, "game.exe")
    csv = tmp_path / "chars.csv"
    csv.write_text(
        "имя;пол;варианты\nДжуди Альварес;ж;Джуди\nДжудн;;\nКерри;x\n", encoding="utf-8-sig"
    )
    result = pc.import_characters_file(store, "game.exe", csv)
    assert result.report.added == ["Джуди Альварес"]
    assert [p.reason for p in result.problems] == ["пол «x»: нужно м, ж или пусто"]
    assert [p.text for p in result.report.skipped] == ["Джудн"]  # matched Джуди: nothing new
    (judy,) = result.stored.profile.characters
    assert judy.gender is Gender.FEMALE and judy.gender_locked and judy.voice is None
    pc.import_characters_file(store, "game.exe", _write(tmp_path, "Ви;;\n"))
    merged = pc.merge_characters_in(store, "game.exe", "Ви", "Джуди")
    (only,) = merged.profile.characters
    assert only.aliases == ["Джуди", "Ви"]
    with pytest.raises(pc.ProfileNotFoundError):
        pc.merge_characters_in(store, "game.exe", "Нет", "Джуди")


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "more.csv"
    path.write_text(text, encoding="utf-8")
    return path
