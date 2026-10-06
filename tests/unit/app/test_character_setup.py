"""Installing the character voices (stage 16): environment, models, zips, status."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from subvoicer.app.character_setup import (
    MARKER,
    CharacterSetup,
    SetupError,
    requirements_hash,
    unpack,
)
from subvoicer.config.models import VOICE_CONVERT_MANIFEST, model_files
from subvoicer.config.paths import AppPaths


def fake_download(
    url: str,
    target: Path,
    sha256: str,
    progress: Callable[[int], None],
    cancelled: Callable[[], bool],
) -> None:
    model = next(m for m in model_files(VOICE_CONVERT_MANIFEST) if m.url == url)
    target.parent.mkdir(parents=True, exist_ok=True)
    if model.unpack:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("nested/model.pth", b"weights")
            zf.writestr("model.index", b"index")
            zf.writestr("readme.txt", b"not taken")
        data = buffer.getvalue()
        with target.open("wb") as out:  # the manifest size decides "present"; a zip may have
            out.seek(model.size - len(data))  # anything in front of it
            out.write(data)
    else:
        with target.open("wb") as out:
            out.truncate(model.size)
    progress(model.size)


class Commands:
    def __init__(self, setup_root: Path) -> None:
        self.calls: list[list[str]] = []
        self.root = setup_root

    def __call__(self, command: list[str]) -> None:
        self.calls.append(command)
        if command[1] == "venv":
            python = Path(command[-1]) / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")


def test_nothing_installed_by_default(tmp_path: Path) -> None:
    status = CharacterSetup(AppPaths(tmp_path)).status()
    assert not status.env and not status.models and not status.installed


def test_install_builds_the_env_downloads_and_unpacks(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    commands = Commands(tmp_path)
    setup = CharacterSetup(paths, fake_download, commands, uv="uv")
    setup.install()
    assert setup.status().installed
    venv, packages, rvc = commands.calls
    assert venv[:4] == ["uv", "venv", "--python", "3.11"]
    assert packages[-2] == "-r" and packages[-1].endswith("requirements.txt")
    assert rvc[-2:] == ["--no-deps", "rvc==0.3.5"]
    vi = paths.voice_convert_dir / "models" / "vi"
    assert sorted(p.name for p in vi.iterdir()) == ["CyberpunkV.zip", "model.index", "model.pth"]
    assert (setup.env_dir / MARKER).read_text(encoding="utf-8") == requirements_hash()
    commands.calls.clear()
    setup.install()  # again: nothing to do
    assert commands.calls == []


def test_changed_requirements_rebuild_the_env(tmp_path: Path) -> None:
    setup = CharacterSetup(AppPaths(tmp_path), fake_download, Commands(tmp_path), uv="uv")
    setup.install()
    (setup.env_dir / MARKER).write_text("old", encoding="utf-8")
    assert not setup.status().env and setup.status().models


def test_without_uv_the_install_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(SetupError, match="Нужен uv"):
        CharacterSetup(AppPaths(tmp_path), fake_download, Commands(tmp_path)).install()


def test_unpack_takes_only_model_files_by_name(tmp_path: Path) -> None:
    archive = tmp_path / "m" / "voice.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../evil.pth", b"x")
        zf.writestr("a/b/added_IVF.index", b"y")
        zf.writestr("run.bat", b"z")
    unpack(archive)
    assert sorted(p.name for p in archive.parent.iterdir()) == [
        "added_IVF.index",
        "evil.pth",
        "voice.zip",
    ]
    assert not (tmp_path / "evil.pth").exists()


def test_a_zip_without_a_model_is_an_error(tmp_path: Path) -> None:
    archive = tmp_path / "voice.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", b"x")
    with pytest.raises(SetupError, match="нет модели"):
        unpack(archive)


def test_manifest_covers_every_character_voice() -> None:
    from subvoicer.config.resources import character_voices

    files = model_files(VOICE_CONVERT_MANIFEST)
    folders = {Path(m.file).parts[0] for m in files}
    assert {v.model for v in character_voices()} <= folders
    assert {"assets/rmvpe.pt", "assets/contentvec/pytorch_model.bin"} <= {m.file for m in files}
    assert 1.7 < sum(m.size for m in files) / 2**30 < 2.0


def test_a_too_long_path_is_refused_before_anything_is_installed(tmp_path: Path) -> None:
    deep = tmp_path / ("x" * 120)
    commands = Commands(tmp_path)
    with pytest.raises(SetupError, match="Слишком длинный путь"):
        CharacterSetup(AppPaths(deep), fake_download, commands, uv="uv").install()
    assert commands.calls == []
