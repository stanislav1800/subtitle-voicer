"""Installing character voices one by one (stage 16, TZ 1.21, 1.24).

Not part of the default install. Into ``voice_convert/``:

- the **engine**, shared by every voice and installed with the first one: ``env/`` — Python
  3.12 with torch and the conversion packages, created by ``uv``: for the NVIDIA GPU
  (``resources/voice_convert/requirements.txt``) or for the CPU (``requirements-cpu.txt``,
  TZ 1.25 — any other computer) (``rvc`` goes in with
  ``--no-deps``: its pins do not build on Windows), and the files of group ``engine`` of
  ``resources/voice_convert/models.toml`` (pitch and speech-feature models);
- a **voice** — the files of its group into ``models/<voice>/``, downloaded with a SHA-256 check
  (a broken download goes on where it stopped); zips are unpacked and removed, so-vits models
  lose their training state (a third of the size, the same sound). ``.installed`` in the folder
  marks a finished voice — the files no longer match the manifest sizes after that.

uv works with its own folders there (TZ 1.24): ``python/`` — the interpreter the environment
runs on, ``uv-cache/`` — the package cache, removed once the environment is built. So all the app
downloads lie under its data folder and goes with it; the user's own uv is not touched.

The environment is marked with the hash of the requirements it was built from — it also tells
what the engine was built for; a changed list rebuilds it. Everything can be cancelled; removing a
voice or the engine deletes its folder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from subvoicer.app.models_setup import Downloader, ModelSetup, Progress
from subvoicer.config.models import VOICE_CONVERT_MANIFEST, ModelFile, model_files
from subvoicer.config.paths import AppPaths
from subvoicer.config.resources import resource_path
from subvoicer.core.errors import ModelsError
from subvoicer.engines.tts.convert_client import HOST_SCRIPT, NO_WINDOW, HostConverter

log = logging.getLogger(__name__)

RVC_PACKAGE = "rvc==0.3.5"
CUDA, ROCM, CPU = "cuda", "rocm", "cpu"  # what the engine is built for (TZ 1.25)
REQUIREMENTS = {
    CUDA: "voice_convert/requirements.txt",
    ROCM: "voice_convert/requirements-rocm.txt",
    CPU: "voice_convert/requirements-cpu.txt",
}
# the app's own version; AMD publishes torch for ROCm on Windows for 3.12 only (TZ 1.25)
PYTHON_VERSION = "3.12"
ENGINE = "engine"  # the group of shared files in the manifest
# the environment on disk, measured (DECISIONS.md, stage 16 and TZ 1.25)
ENV_SIZE = {CUDA: 5_000_000_000, ROCM: 5_300_000_000, CPU: 1_400_000_000}
MARKER = ".subvoicer-requirements"  # in env/: hash of the requirements it was built from
INSTALLED = ".installed"  # in a voice folder: hash of its manifest entries
UNPACKED = (".pth", ".index")  # what is taken out of a model zip
# The deepest file of the environment (in transformers) is ~120 characters below env/; past the
# Windows limit of 260 Python cannot open it (measured, DECISIONS.md, stage 16).
MAX_ENV_PATH = 130

Say = Callable[[str], None]
Report = Callable[[Progress], None]
# a command, extra environment variables; raises on failure or cancel
Runner = Callable[[list[str], Callable[[], bool], Mapping[str, str]], None]


class SetupError(ModelsError):
    """A message for the user, in Russian."""


class SetupCancelled(SetupError):
    pass


@dataclass(frozen=True, slots=True)
class CharacterStatus:
    env: bool  # the engine is installed (environment + shared files)
    models: bool  # at least one voice is installed

    @property
    def installed(self) -> bool:
        return self.env and self.models


@dataclass(frozen=True, slots=True)
class VoicePackage:
    model: str  # the voice's folder (characters.toml ``model``)
    installed: bool
    download: int  # bytes to download for the voice alone
    disk: int  # bytes on disk once installed (measured if installed)


class CharacterSetup:
    def __init__(
        self,
        paths: AppPaths,
        downloader: Downloader | None = None,
        runner: Runner | None = None,
        uv: str | None = None,
        device: str = CUDA,
    ) -> None:
        """``device``: what to build the engine for when it is not there yet."""
        self.paths = paths
        self.device = device
        self.root = paths.voice_convert_dir
        self.env_dir = self.root / "env"
        self.models_dir = self.root / "models"
        self.python_dir = self.root / "python"  # uv's interpreters: the environment runs on one
        self.cache_dir = self.root / "uv-cache"
        self.log_file = self.root / "convert_host.log"
        self._downloader = downloader
        self._runner = runner or _run
        self._uv = uv
        self._cancel = threading.Event()
        self._active: ModelSetup | None = None

    @property
    def python(self) -> Path:
        return self.env_dir / "Scripts" / "python.exe"

    # ------------------------------------------------------------------ what is there

    def env_ready(self) -> bool:
        return self.env_device() is not None

    def env_device(self) -> str | None:
        """What the environment is built for (CUDA, CPU), None — no usable environment."""
        marker = self.env_dir / MARKER
        if not (self.python.is_file() and marker.is_file()):
            return None
        built = marker.read_text(encoding="utf-8").strip()
        return next((d for d in REQUIREMENTS if requirements_hash(d) == built), None)

    def engine_installed(self) -> bool:
        return self.env_ready() and not self._setup(ENGINE).missing()

    def engine_size(self) -> int:
        """Bytes on disk the engine takes (or will take)."""
        if self.engine_installed():
            return _folder_size(self.env_dir) + sum(
                _size(f.path(self.models_dir)) for f in self._files(ENGINE)
            )
        return ENV_SIZE[self.device] + sum(f.size for f in self._files(ENGINE))

    def voices(self) -> list[str]:
        return sorted({f.group for f in model_files(VOICE_CONVERT_MANIFEST) if f.group != ENGINE})

    def installed(self, model: str) -> bool:
        marker = self.models_dir / model / INSTALLED
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip() == self._group_hash(model)
        return self._adopt(model)

    def package(self, model: str) -> VoicePackage:
        files = self._files(model)
        download = sum(f.size for f in files)
        if self.installed(model):
            return VoicePackage(model, True, download, _folder_size(self.models_dir / model))
        return VoicePackage(model, False, download, _expected_disk(files))

    def status(self) -> CharacterStatus:
        return CharacterStatus(
            self.engine_installed(), any(self.installed(v) for v in self.voices())
        )

    # ------------------------------------------------------------------ install / remove

    def install(
        self, model: str, say: Say = lambda _l: None, progress: Report = lambda _p: None
    ) -> None:
        """The voice, and the engine first if needed. Blocks for minutes; raises SetupError
        (SetupCancelled after ``cancel()``)."""
        if model not in self.voices():
            raise SetupError(f"Нет такого голоса персонажа: {model}")
        self._cancel.clear()
        if not self.env_ready():
            self._build_env(say)
        self._download(ENGINE, "Скачиваю движок голосов…", say, progress)
        if self.installed(model):
            say("Голос уже установлен")
            return
        self._download(model, "Скачиваю голос…", say, progress)
        folder = self.models_dir / model
        for file in self._files(model):
            path = file.path(self.models_dir)
            if file.unpack and path.is_file():
                unpack(path)
                path.unlink()  # the zip is not needed once unpacked
        for weights in folder.glob("G*.pth"):  # so-vits: the training state is cut off
            say("Убираю из модели лишнее (данные обучения)…")
            self._check_cancel()
            self._runner(
                [str(self.python), str(HOST_SCRIPT), "--strip", str(weights)],
                self._cancel.is_set,
                {},
            )
        (folder / INSTALLED).write_text(self._group_hash(model), encoding="utf-8")
        say("Голос установлен")

    def cancel(self) -> None:
        self._cancel.set()
        if self._active is not None:
            self._active.cancel()

    def remove(self, model: str) -> None:
        shutil.rmtree(self.models_dir / model, ignore_errors=True)
        log.info("Голос персонажа удалён: %s", model)

    def remove_engine(self) -> None:
        for folder in (self.env_dir, self.python_dir, self.cache_dir):
            shutil.rmtree(folder, ignore_errors=True)
        for file in self._files(ENGINE):
            file.path(self.models_dir).unlink(missing_ok=True)
        log.info("Движок голосов персонажей удалён")

    # ------------------------------------------------------------------ the helper

    def check(self) -> str:
        """Starts the helper once: the name of the GPU, or EngineError with the reason."""
        converter = self.converter()
        try:
            return str(converter.ping().get("device", ""))
        finally:
            converter.close()

    def converter(self) -> HostConverter:
        return HostConverter(self.python, self.models_dir, self.log_file)

    # ------------------------------------------------------------------ internals

    def _files(self, group: str) -> list[ModelFile]:
        return [f for f in model_files(VOICE_CONVERT_MANIFEST) if f.group == group]

    def _setup(self, group: str) -> ModelSetup:
        return ModelSetup(
            self.paths,
            self._downloader,
            VOICE_CONVERT_MANIFEST,
            self.models_dir,
            self._files(group),
        )

    def _group_hash(self, group: str) -> str:
        entries = [f.model_dump() for f in self._files(group)]
        return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()

    def _adopt(self, model: str) -> bool:
        """A voice installed before TZ 1.24 (all at once, zips kept): every file in place and
        the zips unpacked — mark it installed as it is."""
        folder = self.models_dir / model
        files = self._files(model)
        if not files or not folder.is_dir() or self._setup(model).missing():
            return False
        if any(f.unpack for f in files) and not any(folder.glob("*.pth")):
            return False
        (folder / INSTALLED).write_text(self._group_hash(model), encoding="utf-8")
        return True

    def _download(self, group: str, title: str, say: Say, progress: Report) -> None:
        setup = self._setup(group)
        if not setup.missing():
            return
        self._check_cancel()
        say(title)
        self._active = setup
        try:
            setup.download_missing(progress)
        except ModelsError as exc:
            if self._cancel.is_set():
                raise SetupCancelled("Отменено") from exc
            raise
        finally:
            self._active = None

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise SetupCancelled("Отменено")

    def _build_env(self, say: Say) -> None:
        if len(str(self.env_dir)) > MAX_ENV_PATH:
            raise SetupError(
                f"Слишком длинный путь {self.env_dir}: файлы пакетов в нём вышли бы за предел "
                f"Windows в 260 символов. Нужен путь короче {MAX_ENV_PATH} символов"
            )
        uv = self._uv or find_uv()
        if uv is None:
            raise SetupError("Нужен uv (https://docs.astral.sh/uv/): им ставится движок голосов")
        requirements = resource_path(REQUIREMENTS[self.device])
        python = str(self.python)
        target = "процессора" if self.device == CPU else "видеокарты"
        size = f"{ENV_SIZE[self.device] / 1e9:.1f}".replace(".0", "").replace(".", ",")
        say(f"Ставлю движок голосов: Python, torch для {target}, RVC, so-vits-svc (~{size} ГБ)…")
        # from scratch: a half-built or outdated one may have another torch or Python (the
        # packages are still in the cache if a try failed)
        shutil.rmtree(self.env_dir, ignore_errors=True)
        cancelled = self._cancel.is_set
        env = self.uv_env()

        def run(command: list[str]) -> None:
            self._runner(command, cancelled, env)

        run([uv, "venv", "--python", PYTHON_VERSION, str(self.env_dir)])
        run(
            [
                uv,
                "pip",
                "install",
                "--python",
                python,
                "--index-strategy",
                "unsafe-best-match",
                "-r",
                str(requirements),
            ]
        )
        run([uv, "pip", "install", "--python", python, "--no-deps", RVC_PACKAGE])
        (self.env_dir / MARKER).write_text(requirements_hash(self.device), encoding="utf-8")
        shutil.rmtree(self.cache_dir, ignore_errors=True)  # gigabytes of packages, not needed now

    def uv_env(self) -> dict[str, str]:
        """uv's own folders of the app, and only its own Python: one installed elsewhere may
        be removed later and take the environment with it."""
        return {
            "UV_CACHE_DIR": str(self.cache_dir),
            "UV_PYTHON_INSTALL_DIR": str(self.python_dir),
            "UV_PYTHON_PREFERENCE": "only-managed",
        }


def find_uv() -> str | None:
    """``uv.exe`` next to the installed app (TZ 1.24), else the one on PATH (development)."""
    bundled = Path(sys.executable).with_name("uv.exe")
    if getattr(sys, "frozen", False) and bundled.is_file():
        return str(bundled)
    return shutil.which("uv")


def requirements_hash(device: str = CUDA) -> str:
    data = resource_path(REQUIREMENTS[device]).read_bytes()
    # an environment on another Python (3.11 before TZ 1.25) is rebuilt
    return hashlib.sha256(data + RVC_PACKAGE.encode() + PYTHON_VERSION.encode()).hexdigest()


def unpack(archive: Path) -> None:
    """The model files of a zip, flat into its folder (names only: nothing outside it)."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if info.is_dir() or not name.lower().endswith(UNPACKED):
                continue
            target = archive.parent / name
            with zf.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
    if not any(archive.parent.glob("*.pth")):
        raise SetupError(f"В архиве {archive.name} нет модели .pth")
    log.info("Распакован %s", archive)


def _expected_disk(files: list[ModelFile]) -> int:
    """Rough size on disk before install: so-vits weights lose two thirds, zips are unpacked
    (a third bigger, measured on the four voices)."""
    total = 0
    for f in files:
        if f.unpack:
            total += f.size * 4 // 3
        elif Path(f.file).name.startswith("G") and f.file.endswith(".pth"):
            total += f.size // 3
        else:
            total += f.size
    return total


def _size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def _folder_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0


def _run(command: list[str], cancelled: Callable[[], bool], env: Mapping[str, str]) -> None:
    log.info("Запуск: %s", " ".join(command))
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=NO_WINDOW if sys.platform == "win32" else 0,
            cwd=Path.home(),  # a folder that exists, whoever started the app (see convert_client)
            env={**os.environ, **env},
        )
    except OSError as exc:
        raise SetupError(f"Команда не запустилась: {command[0]}: {exc}") from exc
    while True:
        try:
            _, err = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancelled():
                process.kill()
                process.wait()
                raise SetupCancelled("Отменено") from None
    if process.returncode != 0:
        tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        log.error("Команда завершилась с кодом %s: %s", process.returncode, "\n".join(tail))
        raise SetupError(f"Не удалось установить: {' '.join(command[:3])}… (подробности в логе)")


def best_device() -> str:
    """The NVIDIA GPU if its driver answers, else a Radeon that ROCm supports, else the CPU
    (TZ 1.25)."""
    from subvoicer.engines.system.gpu import nvidia_gpu, rocm_gpu, video_adapters

    if nvidia_gpu():
        return CUDA
    return ROCM if rocm_gpu(video_adapters()) else CPU
