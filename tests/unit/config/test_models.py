"""resources/models.toml: what the first run downloads (TZ 4.14)."""

from __future__ import annotations

from pathlib import Path

from subvoicer.config.models import missing, model_files, model_path


def test_manifest_is_valid_and_unique() -> None:
    files = model_files()
    assert {m.id for m in files} == {
        "ocr_det",
        "ocr_rec_eslav",
        "tts_silero",
        "tts_silero_v55",
        "words_ru",
    }
    assert len({m.file for m in files}) == len(files)
    assert all(m.url.startswith("https://") for m in files)


def test_present_by_size(tmp_path: Path) -> None:
    files = model_files()
    assert missing(tmp_path, files) == files
    det = files[0]
    path = det.path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 10)  # an interrupted copy
    assert det in missing(tmp_path, files)
    path.write_bytes(b"x" * det.size)
    assert det not in missing(tmp_path, files)
    assert model_path(tmp_path, "ocr_det") == path
