import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from subvoicer.config.migrations import ProfileFormatError, ProfileTooNewError, migrate
from subvoicer.config.profile import Profile
from subvoicer.config.profile_store import ProfileStore, is_inside
from tests.unit.config.profiles import dialog_zone, profile_data


class Ticker:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 1, 12, 0, 0)

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(tmp_path / "profiles", clock=Ticker())


def profile(**fields: Any) -> Profile:
    return Profile.model_validate(profile_data(**fields))


def test_names_by_exe_and_install_dir(store: ProfileStore) -> None:
    first = store.save(profile(install_dir="C:\\Games\\A"))
    second = store.save(profile(install_dir="D:\\Other\\A"))
    assert first.path.name == "game.exe.json"
    assert second.path.name.startswith("game.exe--")
    assert len(second.path.stem) == len("game.exe--") + 8
    assert store.save(profile(install_dir="c:\\games\\a\\", title="x")).path == first.path
    library = store.save(profile(exe=None, install_dir="E:\\Steam\\X"))
    assert library.path.name.startswith("game--")


def test_backups_keep_five(store: ProfileStore) -> None:
    for title in "abcdefg":
        stored = store.save(profile(title=title))
    backups = sorted(store.backup_dir.glob("game.exe.*.json"))
    assert len(backups) == 5
    assert json.loads(backups[-1].read_text(encoding="utf-8"))["title"] == "f"
    assert store.load(stored.path).title == "g"


def test_interrupted_write_keeps_the_old_profile(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = store.save(profile(title="старый"))

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save(profile(title="новый"), stored.path)
    monkeypatch.undo()
    assert store.load(stored.path).title == "старый"
    assert not list(store.directory.glob("*.tmp"))


def test_matching_a_process(store: ProfileStore) -> None:
    plain = store.save(profile())
    placed = store.save(profile(install_dir="D:\\Games\\G"))
    library = store.save(profile(exe=None, install_dir="E:\\Lib\\Other", title="lib"))
    all_ = store.load_all()
    found = store.match(all_, "GAME.EXE", "D:\\Games\\G\\bin\\game.exe")
    assert found is not None and found.path == placed.path
    found = store.match(all_, "game.exe", "C:\\elsewhere\\game.exe")
    assert found is not None and found.path == plain.path
    assert store.match(all_, "other.exe", "E:\\Lib\\Other\\other.exe") is None
    bindable = store.bindable(all_, "E:\\Lib\\Other\\bin\\other.exe")
    assert bindable is not None and bindable.path == library.path
    bound = store.bind(bindable, "other.exe")
    assert bound.path.name == "other.exe.json" and not library.path.exists()
    assert store.match(store.load_all(), "Other.exe", r"E:\Lib\Other\o.exe") is not None


def test_export_import(store: ProfileStore, tmp_path: Path) -> None:
    stored = store.save(profile(title="экспорт"))
    target = tmp_path / "shared.json"
    store.export(stored.path, target)
    stored.path.unlink()
    imported = store.import_file(target)
    assert imported.path == stored.path and imported.profile.title == "экспорт"


def test_broken_and_too_new_profiles_are_skipped(store: ProfileStore) -> None:
    store.save(profile())
    (store.directory / "bad.json").write_text("{not json", encoding="utf-8")
    too_new = store.directory / "new.json"
    too_new.write_text(json.dumps(profile_data(schema_version=99)), encoding="utf-8")
    odd = store.directory / "odd.json"
    odd.write_text(json.dumps(profile_data(extra=1)), encoding="utf-8")
    loaded = store.load_all()
    assert [p.path.name for p in loaded] == ["game.exe.json"]
    assert json.loads(too_new.read_text(encoding="utf-8"))["schema_version"] == 99  # untouched


def test_update_rereads_hand_edits(store: ProfileStore) -> None:
    stored = store.save(profile(title="a"))
    data = json.loads(stored.path.read_text(encoding="utf-8"))
    data["title"] = "правка руками"
    stored.path.write_text(json.dumps(data), encoding="utf-8")
    updated = store.update(stored.path, lambda p: p.model_copy(update={"narrator_voice": "x"}))
    assert updated.profile.title == "правка руками" and updated.profile.narrator_voice == "x"


# ---------------------------------------------------------------- migrations (artificial v0 -> v1)


def v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """v0 kept one voice for everything: ``voice`` -> narrator and both defaults."""
    voice = data.pop("voice")
    data["narrator_voice"] = voice
    data["default_voices"] = {"male": voice, "female": voice}
    return data


def v0_file() -> dict[str, Any]:
    data = profile_data(schema_version=0, voice="old")
    del data["narrator_voice"], data["default_voices"]
    return data


def test_migration_chain() -> None:
    old = v0_file()
    new = migrate(old, current=1, migrations={0: v0_to_v1})
    assert new["schema_version"] == 1 and new["narrator_voice"] == "old"
    assert old["schema_version"] == 0  # the input is not modified
    with pytest.raises(ProfileTooNewError):
        migrate(profile_data(schema_version=2), current=1, migrations={0: v0_to_v1})
    with pytest.raises(ProfileFormatError, match="Нет миграции"):
        migrate(v0_file(), current=1, migrations={})
    with pytest.raises(ProfileFormatError):
        migrate({"schema_version": "1"}, current=1)


def test_store_migrates_and_backs_up_the_old_file(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles", migrations={0: v0_to_v1}, current_version=1)
    store.directory.mkdir()
    path = store.directory / "game.exe.json"
    path.write_text(json.dumps(v0_file()), encoding="utf-8")
    loaded = store.load(path)
    assert loaded.default_voices.female == "old"
    backup = json.loads((store.backup_dir / "game.exe.v0.json").read_text(encoding="utf-8"))
    assert backup["voice"] == "old"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_is_inside() -> None:
    assert is_inside("D:\\Games\\X\\bin\\x.exe", "d:\\games\\x")
    assert not is_inside("D:\\Games\\XY\\x.exe", "D:\\Games\\X")


def test_v1_profile_gets_word_fixing_on(tmp_path: Path) -> None:
    """TZ 1.23: version 1 -> 2 turns on fixing words with the а/о pair; the rest stays."""
    old = profile_data(schema_version=1, ocr={"engine": "rapidocr", "min_confidence": 0.5})
    new = migrate(old, current=2)
    assert new["schema_version"] == 2
    assert new["ocr"] == {
        "engine": "rapidocr",
        "min_confidence": 0.5,
        "fix_words": True,
        "confusions": [["а", "о"]],
    }
    without_ocr = migrate(profile_data(schema_version=1))
    loaded = Profile.model_validate(without_ocr)
    assert loaded.ocr.fix_words and loaded.ocr.confusions == [("а", "о")]


def test_v2_profile_keeps_the_layout_with_the_most_zones() -> None:
    """TZ 1.26: one layout for every aspect ratio — the one with the most zones is kept, on a
    tie 16:9; a profile without layouts gets an empty one."""
    zone = dialog_zone()
    other = {**dialog_zone(), "id": "other"}
    old = profile_data(schema_version=2)
    del old["layout"]
    old["layouts"] = {
        "16:10": {"zones": []},
        "21:9": {"zones": [zone]},
        "16:9": {"zones": [zone, other]},
    }
    new = migrate(old, current=3)
    assert new["schema_version"] == 3 and "layouts" not in new
    assert [z["id"] for z in new["layout"]["zones"]] == ["dialog", "other"]
    old["layouts"] = {"21:9": {"zones": [zone]}, "16:9": {"zones": [other]}}
    assert [z["id"] for z in migrate(old)["layout"]["zones"]] == ["other"]  # tie: 16:9
    old["layouts"] = {}
    assert migrate(old)["layout"] == {"zones": []}
    assert Profile.model_validate(migrate(old)).layout.zones == []


def test_v3_profile_gets_the_auto_capture() -> None:
    """TZ 1.28: version 3 -> 4 adds the capture mode, window with the screen fallback."""
    new = migrate(profile_data(schema_version=3), current=4)
    assert new["schema_version"] == 4 and new["capture"] == "auto"
    assert Profile.model_validate(new).capture == "auto"
    assert migrate(profile_data(schema_version=3, capture="dxgi"))["capture"] == "dxgi"


def test_v4_zones_keep_their_fixed_rect() -> None:
    """TZ 1.29: version 4 -> 5 — old zones keep their rect, they do not look for plates."""
    new = migrate(profile_data(schema_version=4))
    assert new["schema_version"] == 5
    assert [z["locate"] for z in new["layout"]["zones"]] == ["fixed"]
    assert Profile.model_validate(new).layout.zones[0].locate == "fixed"
    empty = migrate({**profile_data(schema_version=4), "layout": {"zones": []}})
    assert empty["layout"] == {"zones": []}


def test_unknown_capture_mode_is_refused() -> None:
    with pytest.raises(ValueError):
        Profile.model_validate({**profile_data(), "capture": "screen"})
