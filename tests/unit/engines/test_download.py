"""Model download: progress, SHA-256 check, no half-written files (file:// URLs, no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from subvoicer.core.errors import ModelsError
from subvoicer.engines.system.download import CHUNK, Cancelled, download

DATA = bytes(range(256)) * (CHUNK // 64)  # 4 chunks


@pytest.fixture
def source(tmp_path: Path) -> str:
    path = tmp_path / "remote" / "model.onnx"
    path.parent.mkdir()
    path.write_bytes(DATA)
    return path.as_uri()


def test_downloads_with_progress(tmp_path: Path, source: str) -> None:
    target = tmp_path / "models" / "rapidocr" / "model.onnx"
    seen: list[int] = []
    download(source, target, hashlib.sha256(DATA).hexdigest(), progress=seen.append)
    assert target.read_bytes() == DATA
    assert seen[-1] == len(DATA) and seen == sorted(seen) and len(seen) == 4
    assert not list(target.parent.glob("*.part"))


def test_wrong_hash_leaves_nothing(tmp_path: Path, source: str) -> None:
    target = tmp_path / "models" / "model.onnx"
    with pytest.raises(ModelsError, match="SHA-256"):
        download(source, target, "0" * 64)
    assert not target.exists() and not list(target.parent.iterdir())


def test_cancel_and_missing_source(tmp_path: Path, source: str) -> None:
    target = tmp_path / "models" / "model.onnx"
    with pytest.raises(Cancelled):
        download(source, target, "0" * 64, cancelled=lambda: True)
    assert not list(target.parent.iterdir())
    with pytest.raises(ModelsError, match="Не удалось скачать"):
        download((tmp_path / "nope.onnx").as_uri(), target, "0" * 64)
