import numpy as np

from subvoicer.core.models import Rect
from subvoicer.pipeline.vision.crop import crop, pixel_box


def test_pixel_box_rounds_fractions() -> None:
    assert pixel_box(Rect(0.2, 0.82, 0.8, 0.95), 1920, 1080) == (384, 886, 1536, 1026)


def test_pixel_box_is_at_least_one_pixel() -> None:
    assert pixel_box(Rect(0.5, 0.5, 0.5001, 0.5001), 100, 100) == (50, 50, 51, 51)


def test_crop_returns_zone_view() -> None:
    image = np.zeros((100, 200, 4), dtype=np.uint8)
    zone = crop(image, Rect(0.1, 0.5, 0.6, 1.0))
    assert zone.shape == (50, 100, 4)
    zone[:] = 7
    assert image[60, 30, 0] == 7
