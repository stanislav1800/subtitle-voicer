import cv2
import numpy as np
import pytest

from subvoicer.pipeline.vision.masks import (
    MaskPreset,
    MaskSpec,
    bgr_to_hsv,
    build_mask,
    hsv_ranges_around,
    remove_small_components,
)
from tests.fakes.images import ZONE_H, ZONE_W, draw_text

WHITE = (255, 255, 255)
YELLOW_BGR = (58, 216, 255)


def blank(value: int = 20) -> np.ndarray:
    return np.full((ZONE_H, ZONE_W, 3), value, dtype=np.uint8)


def test_bright_on_dark_keeps_text_only() -> None:
    image = draw_text(blank(), "HELLO", WHITE)
    mask = build_mask(image, MaskSpec(MaskPreset.BRIGHT_ON_DARK))
    assert set(np.unique(mask)) == {0, 255}
    assert mask[:, 400:].max() == 0  # right part of the zone has no text


def test_bright_on_dark_accepts_bgra() -> None:
    image = cv2.cvtColor(draw_text(blank(), "HELLO", WHITE), cv2.COLOR_BGR2BGRA)
    assert build_mask(image, MaskSpec(MaskPreset.BRIGHT_ON_DARK)).any()


def test_dark_on_bright() -> None:
    image = draw_text(blank(235), "HELLO", (10, 10, 10), outline=False)
    mask = build_mask(image, MaskSpec(MaskPreset.DARK_ON_BRIGHT))
    assert mask.any()
    assert mask[:, 400:].max() == 0


def test_color_preset_separates_text_colour_from_other_bright_colours() -> None:
    image = blank()
    image = draw_text(image, "HI", YELLOW_BGR)
    cv2.rectangle(image, (400, 10), (560, 70), (255, 200, 60), -1)  # bright light blue block
    ranges = tuple(hsv_ranges_around(bgr_to_hsv(YELLOW_BGR)))
    mask = build_mask(image, MaskSpec(MaskPreset.COLOR, colors_hsv=ranges))
    assert mask[:, :200].any()
    assert mask[:, 400:].max() == 0


def test_color_preset_needs_ranges() -> None:
    with pytest.raises(ValueError):
        MaskSpec(MaskPreset.COLOR)


@pytest.mark.parametrize("value", [20, 235])
def test_adaptive_finds_text_of_either_polarity(value: int) -> None:
    color = (10, 10, 10) if value > 128 else WHITE
    image = draw_text(blank(value), "HELLO", color, outline=False)
    mask = build_mask(image, MaskSpec(MaskPreset.ADAPTIVE))
    text_area = mask[:, :350]
    assert 0.02 < np.count_nonzero(text_area) / text_area.size < 0.5
    assert np.count_nonzero(mask[:, 420:]) == 0


def test_small_noise_components_removed() -> None:
    mask = np.zeros((100, 1000), dtype=np.uint8)
    mask[10:12, 10:12] = 255  # 4 px speck
    mask[40:60, 100:140] = 255  # 800 px blob
    cleaned = remove_small_components(mask, 0.0002 * mask.size)  # 20 px
    assert cleaned[10, 10] == 0
    assert cleaned[50, 120] == 255


def test_white_range() -> None:
    assert hsv_ranges_around((0, 0, 255)) == [
        ((0, 0, 175), (10, 80, 255)),
        ((170, 0, 175), (179, 80, 255)),
    ]


def test_hue_wraps_above_179() -> None:
    ranges = hsv_ranges_around((175, 200, 200))
    assert ranges == [((165, 120, 120), (179, 255, 255)), ((0, 120, 120), (5, 255, 255))]


def test_hue_no_wrap() -> None:
    assert hsv_ranges_around((90, 100, 100)) == [((80, 20, 20), (100, 180, 180))]
