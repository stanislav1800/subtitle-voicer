"""Installing the character voices (stage 16, TZ 1.21): ``subvoicer voices install``.

Not part of the default install. Into ``voice_convert/``:

- ``env/`` — Python 3.11 with torch for the GPU and the conversion packages
  (``resources/voice_convert/requirements.txt``), created by ``uv``; ``rvc`` goes in with
  ``--no-deps`` (its pins do not build on Windows, see the requirements file);
- ``models/`` — the conversion models of ``resources/voice_convert/models.toml``, downloaded with
  a SHA-256 check by the first-run downloader; zips are extracted next to them.

The environment is marked with the hash of the requirements it was built from: a changed list
rebuilds it on the next install. Finally the helper process is started once to check the GPU.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from subvoicer.app.models_setup import Downloader, ModelSetup, Progress
from subvoicer.config.models import VOICE_CONVERT_MANIFEST, model_files
from subvoicer.config.paths import AppPaths
from subvoicer.config.resources import resource_path
from subvoicer.core.errors import ModelsError
from subvoicer.engines.tts.convert_client import HostConverter

log = logging.getLogger(__name__)

PYTHON_VERSION = "3.11"
RVC_PACKAGE = "rvc==0.3.5"
REQUIREMENTS = "voice_convert/requirements.txt"
MARKER = ".subvoicer-requirements"  # in env/: hash of the requirements it was built from
UNPACKED = (".pth", ".index")  # what is taken out of a model zip
# The deepest file of the environment (in transformers) is ~120 characters below env/; past the
# Windows limit of 260 Python cannot open it (measured, DECISIONS.md, stage 16).
MAX_ENV_PATH = 130

Runner = Callable[[list[str]], None]  # runs a command, raises on a non-zero exit


class SetupError(ModelsError):
    """A message for the user, in Russian."""


@dataclass(frozen=True, slots=True)
class CharacterStatus:
    env: bool  # the environment is built from the current requirements
    models: bool  # every model file is in place (and unpacked)

    @property
    def installed(self) -> bool:
        return self.env and self.models


class CharacterSetup:
    def __init__(
        self,
        paths: AppPaths,
        downloader: Downloader | None = None,
        runner: Runner | None = None,
        uv: str | None = None,
    ) -> None:
        self.paths = paths
        self.root = paths.voice_convert_dir
        self.env_dir = self.root / "env"
        self.models_dir = self.root / "models"
        self.log_file = self.root / "convert_host.log"
        self._models = ModelSetup(paths, downloader, VOICE_CONVERT_MANIFEST, self.models_dir)
        self._runner = runner or _run
        self._uv = uv

    @property
    def python(self) -> Path:
        return self.env_dir / "Scripts" / "python.exe"

    def status(self) -> CharacterStatus:
        marker = self.env_dir / MARKER
        env = (
            self.python.is_file()
            and marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == requirements_hash()
        )
        models = not self._models.missing() and all(
            self._unpacked(m.path(self.models_dir))
            for m in model_files(VOICE_CONVERT_MANIFEST)
            if m.unpack
        )
        return CharacterStatus(env, models)

    def install(
        self,
        say: Callable[[str], None] = lambda _line: None,
        progress: Callable[[Progress], None] = lambda _p: None,
    ) -> None:
        """Blocks for minutes (a ~5 GB environment, ~1.8 GB of models); raises SetupError."""
        status = self.status()
        if not status.env:
            self._build_env(say)
        else:
            say("Окружение уже установлено")
        say("Скачиваю модели голосов…")
        self._models.download_missing(progress)
        for model in model_files(VOICE_CONVERT_MANIFEST):
            if model.unpack and not self._unpacked(model.path(self.models_dir)):
                unpack(model.path(self.models_dir))
        say("Модели на месте")

    def check(self) -> str:
        """Starts the helper once: the name of the GPU, or EngineError with the reason."""
        converter = self.converter()
        try:
            return str(converter.ping().get("device", ""))
        finally:
            converter.close()

    def converter(self) -> HostConverter:
        return HostConverter(self.python, self.models_dir, self.log_file)

    def _build_env(self, say: Callable[[str], None]) -> None:
        if len(str(self.env_dir)) > MAX_ENV_PATH:
            raise SetupError(
                f"Слишком длинный путь {self.env_dir}: файлы пакетов в нём вышли бы за предел "
                f"Windows в 260 символов. Нужен путь короче {MAX_ENV_PATH} символов"
            )
        uv = self._uv or shutil.which("uv")
        if uv is None:
            raise SetupError("Нужен uv (https://docs.astral.sh/uv/): им ставится окружение голосов")
        requirements = resource_path(REQUIREMENTS)
        python = str(self.python)
        say(f"Создаю окружение Python {PYTHON_VERSION} в {self.env_dir}…")
        (self.env_dir / MARKER).unlink(missing_ok=True)
        self._runner(
            [uv, "venv", "--python", PYTHON_VERSION, "--allow-existing", str(self.env_dir)]
        )
        say("Ставлю пакеты (torch для видеокарты, RVC, so-vits-svc) — это несколько гигабайт…")
        self._runner(
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
        self._runner([uv, "pip", "install", "--python", python, "--no-deps", RVC_PACKAGE])
        (self.env_dir / MARKER).write_text(requirements_hash(), encoding="utf-8")

    def _unpacked(self, archive: Path) -> bool:
        return any(archive.parent.glob("*.pth"))


def requirements_hash() -> str:
    data = resource_path(REQUIREMENTS).read_bytes()
    return hashlib.sha256(data + RVC_PACKAGE.encode()).hexdigest()


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


def _run(command: list[str]) -> None:
    log.info("Запуск: %s", " ".join(command))
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SetupError(f"Команда не выполнилась: {' '.join(command[:3])}…: {exc}") from exc
