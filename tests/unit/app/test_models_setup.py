"""First run: missing models are downloaded once, with overall progress."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from subvoicer.app.models_setup import ModelSetup, Progress, engine_options
from subvoicer.config.models import model_files
from subvoicer.config.paths import AppPaths


class FakeDownloader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(
        self,
        url: str,
        target: Path,
        sha256: str,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> None:
        self.urls.append(url)
        size = next(m.size for m in model_files() if m.url == url)
        target.parent.mkdir(parents=True, exist_ok=True)
        progress(size // 2)
        with target.open("wb") as out:
            out.truncate(size)
        progress(size)


def test_downloads_only_missing_once(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    fake = FakeDownloader()
    setup = ModelSetup(paths, fake)
    seen: list[Progress] = []
    setup.download_missing(seen.append)
    assert len(fake.urls) == 4 and setup.missing() == []
    total = sum(m.size for m in model_files())
    assert seen[-1].done == total and seen[-1].fraction == 1.0
    assert [p.done for p in seen] == sorted(p.done for p in seen)
    setup.download_missing(seen.append)  # nothing again
    assert len(fake.urls) == 4


def test_engine_options_point_into_models(tmp_path: Path) -> None:
    ocr, tts = engine_options(AppPaths(tmp_path))
    assert ocr["model"] == "eslav"
    assert Path(ocr["rec_path"]).parent == tmp_path / "models" / "rapidocr"
    assert {k: Path(v).name for k, v in tts["models"].items()} == {
        "v5_cis_base": "v5_cis_base.pt",
        "v5_5_ru": "v5_5_ru.pt",
    }
