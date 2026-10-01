"""OCR quality on real game frames with the default settings (TZ 1.17 measure)."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest
from rapidfuzz.distance import Levenshtein

from subvoicer.engines.ocr.rapid import RapidOcrEngine
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec, build_mask

pytestmark = [pytest.mark.models, pytest.mark.windows]

CYBERPUNK = Path(__file__).parent.parent / "fixtures" / "real" / "cyberpunk"
CYBERPUNK_TEXT = (((84, 58, 144), (104, 218, 255)),)
MAX_CER = 0.08  # 5.6% measured on 2026-10-01 (eslav, no upscale)


def squash(text: str) -> str:
    return " ".join(text.split())


def test_cyberpunk_cer_with_defaults() -> None:
    if not RapidOcrEngine.is_available():
        pytest.skip("RapidOCR не установлен")
    engine = RapidOcrEngine()
    engine.load()
    runner = OcrRunner(engine)
    distance = length = 0
    for path in sorted(CYBERPUNK.glob("*.png")):
        image = cv2.imread(str(path))
        mask = build_mask(image, MaskSpec(MaskPreset.COLOR, colors_hsv=CYBERPUNK_TEXT))
        reference = squash(path.with_suffix(".txt").read_text(encoding="utf-8"))
        attempt = runner.attempt(image, mask, OcrInput.RAW)
        text = squash(" ".join(line.text for line in attempt.lines))
        distance += Levenshtein.distance(text, reference)
        length += len(reference)
    assert length > 0
    assert distance / length <= MAX_CER
