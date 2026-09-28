"""OcrEngine contract, run for every registered engine available on this machine."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from subvoicer.core.models import OcrResult
from subvoicer.engines import registry

pytestmark = [pytest.mark.models, pytest.mark.windows]

TEXT = "Встретимся у моста"
FONT = Path("C:/Windows/Fonts/arial.ttf")


def engine_ids() -> list[str]:
    return [eid for eid in registry.entries("ocr") if registry.resolve("ocr", eid).is_available()]


@pytest.fixture(scope="module", params=engine_ids())
def engine(request: pytest.FixtureRequest) -> Iterator[object]:
    instance = registry.resolve("ocr", request.param)()
    instance.load()
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def image_bgr() -> np.ndarray:
    if not FONT.exists():
        pytest.skip("нет шрифта arial.ttf")
    image = Image.new("RGB", (700, 100), (20, 20, 20))
    ImageDraw.Draw(image).text((30, 25), TEXT, font=ImageFont.truetype(str(FONT), 44), fill="white")
    return np.asarray(image)[:, :, ::-1].copy()


def test_engine_metadata(engine: object) -> None:
    assert isinstance(engine.id, str)  # type: ignore[attr-defined]
    assert isinstance(engine.display_name, str)  # type: ignore[attr-defined]
    assert "ru" in engine.capabilities.languages  # type: ignore[attr-defined]


def test_recognizes_russian_line(engine: object, image_bgr: np.ndarray) -> None:
    result = engine.recognize(image_bgr)  # type: ignore[attr-defined]
    assert isinstance(result, OcrResult)
    assert " ".join(line.text for line in result.lines) == TEXT
    for line in result.lines:
        assert 0.0 <= line.confidence <= 1.0
        x1, y1, x2, y2 = line.bbox
        assert 0 <= x1 < x2 <= image_bgr.shape[1] + 2
        assert 0 <= y1 < y2 <= image_bgr.shape[0] + 2


@pytest.mark.parametrize("form", ["gray", "bgra"])
def test_accepts_gray_and_bgra(engine: object, image_bgr: np.ndarray, form: str) -> None:
    import cv2

    code = cv2.COLOR_BGR2GRAY if form == "gray" else cv2.COLOR_BGR2BGRA
    result = engine.recognize(cv2.cvtColor(image_bgr, code))  # type: ignore[attr-defined]
    assert " ".join(line.text for line in result.lines) == TEXT


def test_blank_image_gives_no_lines(engine: object) -> None:
    result = engine.recognize(np.zeros((100, 700, 3), dtype=np.uint8))  # type: ignore[attr-defined]
    assert result.lines == ()
