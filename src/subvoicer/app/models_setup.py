"""First run (TZ 4.14): download the missing models into ``models/`` with progress; the
engines then load them from there."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from subvoicer.config.models import MODELS_MANIFEST, ModelFile, missing, model_files, model_path
from subvoicer.config.paths import AppPaths

log = logging.getLogger(__name__)

Downloader = Callable[..., None]  # (url, target, sha256, progress=, cancelled=)


@dataclass(frozen=True, slots=True)
class Progress:
    title: str  # the file being downloaded
    index: int  # 1-based
    count: int
    done: int  # bytes of all files
    total: int

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 1.0


class ModelSetup:
    def __init__(
        self,
        paths: AppPaths,
        downloader: Downloader | None = None,
        manifest: str = MODELS_MANIFEST,
        models_dir: Path | None = None,
        files: list[ModelFile] | None = None,
    ) -> None:
        """``manifest``/``models_dir``: the first-run models by default; the character voices
        use the same downloader with their own list and folder (stage 16)."""
        self._paths = paths
        self._downloader = downloader
        self._manifest = manifest
        self._files = files  # a part of the manifest (one character voice), else all of it
        self._models_dir = models_dir or paths.models_dir
        self._cancel = threading.Event()

    def missing(self) -> list[ModelFile]:
        files = self._files if self._files is not None else model_files(self._manifest)
        return missing(self._models_dir, files)

    def cancel(self) -> None:
        self._cancel.set()

    def download_missing(self, progress: Callable[[Progress], None] = lambda _p: None) -> None:
        """Blocks until every model is in place; raises DownloadError with a user message."""
        files = self.missing()
        if not files:
            return
        self._cancel.clear()
        download = self._downloader or _default_downloader()
        total = sum(m.size for m in files)
        before = 0
        log.info("Скачиваю модели: %s", ", ".join(m.id for m in files))
        for index, model in enumerate(files, start=1):

            def report(
                done: int, model: ModelFile = model, index: int = index, before: int = before
            ) -> None:
                progress(Progress(model.title, index, len(files), before + done, total))

            report(0)
            download(
                model.url,
                model.path(self._models_dir),
                model.sha256,
                progress=report,
                cancelled=self._cancel.is_set,
            )
            before += model.size
        progress(Progress("", len(files), len(files), total, total))


def _default_downloader() -> Downloader:
    from subvoicer.engines.system.download import download

    return download


def engine_options(paths: AppPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    """RapidOCR and Silero options pointing at the downloaded files."""
    models = paths.models_dir
    ocr = {
        "model": "eslav",
        "det_path": str(model_path(models, "ocr_det")),
        "rec_path": str(model_path(models, "ocr_rec_eslav")),
    }
    tts = {
        "models": {
            "v5_cis_base": str(model_path(models, "tts_silero")),
            "v5_5_ru": str(model_path(models, "tts_silero_v55")),
        }
    }
    return ocr, tts


class ConsoleProgress:
    """CLI: one updating line, redrawn once per percent."""

    def __init__(self) -> None:
        self._percent = -1

    def __call__(self, progress: Progress) -> None:
        percent = int(progress.fraction * 100)
        if percent == self._percent:
            return
        self._percent = percent
        line = (
            f"\rСкачиваю модели: {percent:3d}% — {progress.done / 2**20:.1f} из "
            f"{progress.total / 2**20:.1f} МБ ({progress.index}/{progress.count}) {progress.title}"
        )
        done = progress.done >= progress.total
        print(line.ljust(110)[:110], end="\n" if done else "", file=sys.stderr, flush=True)
