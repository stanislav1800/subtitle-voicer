import numpy as np
import pytest

from subvoicer.pipeline.ocr.inputs import OcrInput, build_input, estimate_line_height


def zone(line_height: int) -> tuple[np.ndarray, np.ndarray]:
    crop = np.full((100, 300, 3), 40, dtype=np.uint8)
    mask = np.zeros((100, 300), dtype=np.uint8)
    mask[20 : 20 + line_height, 50:250] = 255
    crop[mask > 0] = 250
    return crop, mask


def test_line_height_is_median_band() -> None:
    mask = np.zeros((100, 10), dtype=np.uint8)
    mask[5:25, 0] = 255  # 20
    mask[40:62, 0] = 255  # 22
    mask[80:81, 0] = 255  # 1 px noise row — ignored
    assert estimate_line_height(mask) == 21


def test_line_height_of_empty_mask() -> None:
    assert estimate_line_height(np.zeros((10, 10), np.uint8)) is None


def test_raw_is_bgr_crop() -> None:
    crop, mask = zone(40)
    prepared = build_input(
        np.dstack([crop, np.full(crop.shape[:2], 255, np.uint8)]), mask, OcrInput.RAW
    )
    assert prepared.image.shape == (100, 300, 3)
    assert prepared.scale == 1.0


def test_masked_paints_outside_dilated_mask_black() -> None:
    crop, mask = zone(40)
    crop[:] = 200
    image = build_input(crop, mask, OcrInput.MASKED).image
    assert image[30, 100].tolist() == [200, 200, 200]  # inside text
    assert image[18, 100].tolist() == [200, 200, 200]  # 2 px dilation above the text
    assert image[10, 100].tolist() == [0, 0, 0]  # far outside


def test_mask_is_black_text_on_white() -> None:
    crop, mask = zone(40)
    image = build_input(crop, mask, OcrInput.MASK).image
    assert image.ndim == 2
    assert image[30, 100] == 0
    assert image[5, 5] == 255


@pytest.mark.parametrize("kind", list(OcrInput))
def test_small_text_is_upscaled(kind: OcrInput) -> None:
    crop, mask = zone(20)
    prepared = build_input(crop, mask, kind, upscale=2.0, min_line_height=32)
    assert prepared.scale == 2.0
    assert prepared.image.shape[:2] == (200, 600)


def test_large_text_is_not_upscaled() -> None:
    crop, mask = zone(40)
    assert build_input(crop, mask, OcrInput.RAW).scale == 1.0


def test_upscale_disabled() -> None:
    crop, mask = zone(20)
    assert build_input(crop, mask, OcrInput.RAW, upscale=1.0).scale == 1.0
