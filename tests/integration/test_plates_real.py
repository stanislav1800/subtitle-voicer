"""Subtitle plates on the user's real frames of three games (TZ 4.2.2, stage 13).

Labels: ``tests/fixtures/real/<game>/plates.json`` (boxes in pixels of the lower half of the
frame); the frames themselves (~100 MB) live only on the user's machine in
``tests/fixtures/plates/<game>/`` and the detector model in the app's ``models/`` — without
them the test is skipped. A found plate matches a labelled one at IoU >= 0.6 (labels are
10-15 px loose), or when it covers the label and is not much bigger (labels drawn around
the text on a letterbox bar). Semi-transparent plates are reported, not required.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from subvoicer.config.paths import AppPaths
from subvoicer.pipeline.vision.plates import Box
from subvoicer.sources.zone_ocr import PlateSearch, locate

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
DET_MODEL = AppPaths.default().models_dir / "rapidocr" / "ch_PP-OCRv5_det_mobile.onnx"
SEARCH_TOP = 0.55  # the default search area of the zone starts here (TZ 4.2.2)
# game -> (plates that must be found, of how many), extra boxes allowed
EXPECTED = {"alan_wake": (31, 31, 0), "cyberpunk": (11, 12, 1), "silent_hill_f": (12, 12, 0)}


def matches(found: Box, label: Box) -> bool:
    fx1, fy1, fx2, fy2 = found
    lx1, ly1, lx2, ly2 = label
    common = max(0, min(fx2, lx2) - max(fx1, lx1)) * max(0, min(fy2, ly2) - max(fy1, ly1))
    a, b = (fx2 - fx1) * (fy2 - fy1), (lx2 - lx1) * (ly2 - ly1)
    return common >= 0.6 * (a + b - common) or (common >= 0.9 * b and common >= 0.25 * a)


@pytest.fixture(scope="module")
def detector():  # type: ignore[no-untyped-def]
    if not DET_MODEL.is_file():
        pytest.skip("нет модели детектора текста (её скачивает приложение)")
    from subvoicer.engines.ocr.rapid import RapidOcrEngine

    engine = RapidOcrEngine(det_path=str(DET_MODEL))
    engine.load()
    return engine


@pytest.mark.parametrize("game", list(EXPECTED))
def test_plates_on_real_frames(game: str, detector) -> None:  # type: ignore[no-untyped-def]
    folder = ROOT / "plates" / game
    if not folder.is_dir():
        pytest.skip(f"нет кадров {folder} (только у пользователя)")
    labels = json.loads((ROOT / "real" / game / "plates.json").read_text(encoding="utf-8"))
    found_count = extra = 0
    misses = []
    for name, entry in labels.items():
        lower = cv2.imread(str(folder / f"{name}.png"))
        offset = round((SEARCH_TOP - 0.5) * 2 * lower.shape[0])
        search = lower[offset:]
        plates = locate(search, PlateSearch(detector))  # as the app looks for them
        found = [(x1, y1 + offset, x2, y2 + offset) for x1, y1, x2, y2 in (p.box for p in plates)]
        boxes = [(x, y, x + w, y + h) for x, y, w, h in (p["box"] for p in entry["plates"])]
        extra += sum(not any(matches(f, b) for b in boxes) for f in found)
        if entry.get("transparent"):
            continue
        hits = [any(matches(f, b) for f in found) for b in boxes]
        found_count += sum(hits)
        misses += [f"{name}: {b}" for b, hit in zip(boxes, hits, strict=True) if not hit]
    need, total, allowed = EXPECTED[game]
    assert found_count >= need, f"{game}: найдено {found_count} из {total}, пропущены {misses}"
    assert extra <= allowed, f"{game}: лишних рамок {extra}"
