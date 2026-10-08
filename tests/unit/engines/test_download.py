"""Model download: progress, SHA-256 check, no half-written files (file:// URLs, no network)."""

from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path
from typing import ClassVar

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
    assert not target.exists()  # only the part stays, to be continued (TZ 1.24)
    assert [p.name for p in target.parent.iterdir()] == ["model.onnx.part"]
    with pytest.raises(ModelsError, match="Не удалось скачать"):
        download((tmp_path / "nope.onnx").as_uri(), target, "0" * 64)


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves DATA, honouring "Range: bytes=N-" like the model hosts do."""

    requests: ClassVar[list[str | None]] = []

    def do_GET(self) -> None:
        wanted = self.headers.get("Range")
        RangeHandler.requests.append(wanted)
        start = int(wanted.removeprefix("bytes=").rstrip("-")) if wanted else 0
        body = DATA[start:]
        self.send_response(206 if wanted else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def test_a_cancelled_download_is_continued(tmp_path: Path) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/model.onnx"
    target = tmp_path / "models" / "model.onnx"
    sha = hashlib.sha256(DATA).hexdigest()
    seen: list[int] = []
    try:
        with pytest.raises(Cancelled):
            download(url, target, sha, progress=seen.append, cancelled=lambda: len(seen) >= 2)
        kept = target.with_name("model.onnx.part").stat().st_size
        assert 0 < kept < len(DATA)
        seen.clear()
        download(url, target, sha, progress=seen.append)
    finally:
        server.shutdown()
    assert target.read_bytes() == DATA
    assert RangeHandler.requests[-1] == f"bytes={kept}-"
    assert seen[0] == kept and seen[-1] == len(DATA)
