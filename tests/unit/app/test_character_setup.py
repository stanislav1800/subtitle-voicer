"""Installing character voices one by one (stage 16, TZ 1.21, 1.24): the engine with the first
voice, unpacking, stripping, removing, cancelling, an install from before TZ 1.24."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from subvoicer.app.character_setup import (
    INSTALLED,
    MARKER,
    CharacterSetup,
    SetupCancelled,
    SetupError,
    requirements_hash,
    unpack,
)
from subvoicer.config.models import VOICE_CONVERT_MANIFEST, model_files
from subvoicer.config.paths import AppPaths
from subvoicer.engines.system.download import Cancelled


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
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[Mapping[str, str]] = []

    def __call__(
        self, command: list[str], cancelled: Callable[[], bool], env: Mapping[str, str]
    ) -> None:
        self.calls.append(command)
        self.envs.append(env)
        if "UV_CACHE_DIR" in env:  # uv fills its cache while it installs
            cache = Path(env["UV_CACHE_DIR"])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "wheel").write_bytes(b"x")
        if command[1] == "venv":
            python = Path(command[-1]) / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
        if "--strip" in command:  # the helper cuts the training state off
            Path(command[-1]).write_bytes(b"slim")


@pytest.fixture
def commands() -> Commands:
    return Commands()


@pytest.fixture
def setup(tmp_path: Path, commands: Commands) -> CharacterSetup:
    return CharacterSetup(AppPaths(tmp_path), fake_download, commands, uv="uv")


def test_nothing_installed_by_default(setup: CharacterSetup) -> None:
    assert not setup.engine_installed() and not setup.status().installed
    assert setup.voices() == ["ciri", "geralt", "johnny", "vi"]
    assert not any(setup.installed(v) for v in setup.voices())
    vi = setup.package("vi")
    assert not vi.installed and vi.download == 92_393_456 and vi.disk > vi.download


def test_a_voice_needs_the_engine_first(setup: CharacterSetup, commands: Commands) -> None:
    """TZ 1.25: the engine is installed on its own, voices only after it."""
    with pytest.raises(SetupError, match="Сначала установите движок"):
        setup.install("vi")
    assert commands.calls == [] and not setup.installed("vi")


def test_the_engine_then_a_voice(setup: CharacterSetup, commands: Commands) -> None:
    said: list[str] = []
    setup.install_engine(said.append)
    assert setup.engine_installed() and not setup.status().installed
    assert "Движок установлен" in said
    setup.install("vi", said.append)
    venv, packages, rvc = commands.calls
    assert venv[:4] == ["uv", "venv", "--python", "3.12"] and "--allow-existing" not in venv
    assert packages[-2] == "-r" and packages[-1].endswith("requirements.txt")
    assert rvc[-2:] == ["--no-deps", "rvc==0.3.5"]
    assert setup.engine_installed() and setup.installed("vi")
    assert not setup.installed("ciri") and setup.status().installed
    vi = setup.models_dir / "vi"
    assert sorted(p.name for p in vi.iterdir()) == [INSTALLED, "model.index", "model.pth"]
    assert (setup.env_dir / MARKER).read_text(encoding="utf-8") == requirements_hash()
    assert "Голос установлен" in said


def test_uv_uses_the_app_own_folders_and_the_cache_is_cleaned(
    setup: CharacterSetup, commands: Commands
) -> None:
    """TZ 1.24: what the engine downloads stays in the app data, the user's uv is untouched."""
    setup.install_engine()
    for env in commands.envs:
        assert env["UV_CACHE_DIR"] == str(setup.root / "uv-cache")
        assert env["UV_PYTHON_INSTALL_DIR"] == str(setup.root / "python")
        assert env["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert not setup.cache_dir.exists()
    setup.python_dir.mkdir()
    setup.remove_engine()
    assert not setup.python_dir.exists()


def test_more_voices_reuse_the_engine_and_so_vits_is_stripped(
    setup: CharacterSetup, commands: Commands
) -> None:
    setup.install_engine()
    setup.install("vi")
    commands.calls.clear()
    setup.install("ciri")
    (strip,) = commands.calls  # no environment again, only the strip of G.pth
    assert strip[-2:] == ["--strip", str(setup.models_dir / "ciri" / "G.pth")]
    assert (setup.models_dir / "ciri" / "G.pth").read_bytes() == b"slim"
    assert setup.installed("ciri")  # although G.pth no longer matches the manifest size
    package = setup.package("ciri")
    assert package.installed and package.disk < package.download


def test_remove_a_voice_and_the_engine(setup: CharacterSetup) -> None:
    setup.install_engine()
    setup.install("vi")
    setup.install("johnny")
    setup.remove("vi")
    assert not setup.installed("vi") and setup.installed("johnny") and setup.engine_installed()
    setup.remove_engine()
    assert not setup.engine_installed() and not setup.env_dir.exists()
    assert not setup.status().installed  # a voice without the engine is of no use


def test_cancel_stops_and_keeps_what_was_downloaded(tmp_path: Path, commands: Commands) -> None:
    holder: list[CharacterSetup] = []

    def cancelling(url: str, target: Path, sha256: str, progress, cancelled) -> None:  # type: ignore[no-untyped-def]
        if cancelled():  # like the real one: stops at the next chunk
            raise Cancelled("Скачивание отменено")
        fake_download(url, target, sha256, progress, cancelled)
        holder[0].cancel()  # the user pressed «Отмена» during the first file

    setup = CharacterSetup(AppPaths(tmp_path), cancelling, commands, uv="uv")
    holder.append(setup)
    with pytest.raises(SetupCancelled):
        setup.install_engine()
    assert not setup.engine_installed()
    assert (setup.models_dir / "assets" / "rmvpe.pt").is_file()  # kept for the next time


def test_an_install_from_before_one_by_one_is_adopted(tmp_path: Path) -> None:
    """TZ 1.24: every file in place (zips kept, G.pth not stripped) — installed as it is."""
    setup = CharacterSetup(AppPaths(tmp_path), fake_download, Commands(), uv="uv")
    for model in model_files(VOICE_CONVERT_MANIFEST):
        if model.group in ("ciri", "vi"):
            fake_download(
                model.url,
                model.path(setup.models_dir),
                model.sha256,
                lambda _n: None,
                lambda: False,
            )
    (setup.models_dir / "vi" / "model.pth").write_bytes(b"unpacked earlier")
    assert setup.installed("ciri") and setup.installed("vi")
    assert (setup.models_dir / "ciri" / INSTALLED).is_file()


def test_unknown_voice(setup: CharacterSetup) -> None:
    with pytest.raises(SetupError, match="Нет такого голоса"):
        setup.install("kratos")


def test_without_uv_the_install_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(SetupError, match="Нужен uv"):
        CharacterSetup(AppPaths(tmp_path), fake_download, Commands()).install_engine()


def test_a_too_long_path_is_refused_before_anything_is_installed(tmp_path: Path) -> None:
    deep = tmp_path / ("x" * 120)
    commands = Commands()
    with pytest.raises(SetupError, match="Слишком длинный путь"):
        CharacterSetup(AppPaths(deep), fake_download, commands, uv="uv").install_engine()
    assert commands.calls == []


def test_unpack_takes_only_model_files_by_name(tmp_path: Path) -> None:
    archive = tmp_path / "m" / "voice.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../evil.pth", b"x")
        zf.writestr("a/b/added_IVF.index", b"y")
        zf.writestr("run.bat", b"z")
    unpack(archive)
    names = sorted(p.name for p in archive.parent.iterdir())
    assert names == ["added_IVF.index", "evil.pth", "voice.zip"]
    assert not (tmp_path / "evil.pth").exists()


def test_a_zip_without_a_model_is_an_error(tmp_path: Path) -> None:
    archive = tmp_path / "voice.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", b"x")
    with pytest.raises(SetupError, match="нет модели"):
        unpack(archive)


def test_manifest_groups_every_character_voice() -> None:
    from subvoicer.config.resources import character_voices

    files = model_files(VOICE_CONVERT_MANIFEST)
    groups = {m.group for m in files}
    assert groups == {"engine"} | {v.model for v in character_voices()}
    assert all(Path(m.file).parts[0] in (m.group, "assets") for m in files)
    assert 1.7 < sum(m.size for m in files) / 2**30 < 2.0


def test_the_engine_for_the_cpu(tmp_path: Path, commands: Commands) -> None:
    """TZ 1.25: without an NVIDIA GPU the engine is built with torch for the CPU, and the marker
    tells what it was built for."""
    from subvoicer.app.character_setup import CPU, CUDA, ENV_SIZE

    setup = CharacterSetup(AppPaths(tmp_path), fake_download, commands, uv="uv", device=CPU)
    assert setup.env_device() is None
    assert setup.engine_size() < ENV_SIZE[CUDA]
    setup.install_engine()
    assert commands.calls[1][-1].endswith("requirements-cpu.txt")
    assert setup.env_device() == CPU and setup.engine_installed()
    # built for the CPU, it still works when the app would pick the GPU now
    assert CharacterSetup(AppPaths(tmp_path), device=CUDA).env_device() == CPU


def test_the_two_requirement_lists_differ_only_in_torch() -> None:
    from subvoicer.app.character_setup import REQUIREMENTS
    from subvoicer.config.resources import resource_path

    def packages(device: str) -> set[str]:
        lines = resource_path(REQUIREMENTS[device]).read_text(encoding="utf-8").splitlines()
        return {line.split("==")[0] for line in lines if "==" in line and "torch" not in line}

    assert packages("cuda") == packages("cpu")
