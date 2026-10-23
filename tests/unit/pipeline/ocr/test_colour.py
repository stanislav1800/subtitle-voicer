import cv2
import numpy as np

from subvoicer.core.models import OcrLine
from subvoicer.pipeline.ocr.colour import colour_lines, colour_share
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec, build_mask

CYAN = ((80, 90, 170), (100, 255, 255))
BGR_CYAN, BGR_YELLOW = (230, 210, 40), (40, 210, 240)


def scene() -> np.ndarray:
    """A dark frame: a cyan subtitle line with a red name, a yellow «15M» below."""
    image = np.full((120, 400, 3), 25, dtype=np.uint8)
    for x in range(60, 360, 8):
        image[20:40, x : x + 4] = BGR_CYAN
    for x in range(10, 50, 8):
        image[20:40, x : x + 4] = (40, 40, 230)  # the name «Ви:» in red
    for x in range(180, 220, 8):
        image[80:95, x : x + 4] = BGR_YELLOW
    return image


def mask_of(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    assert hsv[25, 61, 0] in range(80, 101)  # the test colour is the zone's cyan
    return build_mask(image, MaskSpec(MaskPreset.COLOR, colors_hsv=(CYAN,)))


def test_lines_of_another_colour_are_dropped() -> None:
    image = scene()
    subtitle = OcrLine("Ви: Видишь, какая-то польза от тебя.", 0.95, (5, 15, 365, 45))
    marker = OcrLine("15M", 0.9, (175, 75, 225, 100))
    kept, dropped = colour_lines([subtitle, marker], image, mask_of(image))
    assert kept == [subtitle] and dropped == [marker]


def test_a_line_with_too_few_letter_pixels_is_kept() -> None:
    image = scene()
    assert colour_share(image, mask_of(image), (370, 100, 380, 110)) is None
    assert colour_share(image, mask_of(image), (500, 0, 600, 10)) is None  # out of the crop
