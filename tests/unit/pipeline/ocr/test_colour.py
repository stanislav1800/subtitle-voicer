import numpy as np

from subvoicer.core.models import OcrLine
from subvoicer.pipeline.ocr.colour import colour_lines, density
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec, build_mask

CYAN = ((80, 90, 170), (100, 255, 255))
BGR_CYAN, BGR_YELLOW = (230, 210, 40), (40, 210, 240)
SUBTITLE = OcrLine("Ви: Видишь, какая-то польза от тебя.", 0.95, (5, 15, 365, 45))
MARKER = OcrLine("15M", 0.9, (175, 75, 225, 100))


def scene(background: tuple[int, int, int] = (25, 25, 25)) -> np.ndarray:
    """A cyan subtitle line with a red name, a yellow «15M» below."""
    image = np.full((120, 400, 3), background, dtype=np.uint8)
    for x in range(60, 360, 8):
        image[20:40, x : x + 4] = BGR_CYAN
    for x in range(10, 50, 8):
        image[20:40, x : x + 4] = (40, 40, 230)  # the name «Ви:» in red
    for x in range(180, 220, 8):
        image[80:95, x : x + 4] = BGR_YELLOW
    return image


def cyan_mask(image: np.ndarray) -> np.ndarray:
    return build_mask(image, MaskSpec(MaskPreset.COLOR, colors_hsv=(CYAN,)))


def test_rows_of_another_colour_are_dropped() -> None:
    image = scene()
    kept, dropped = colour_lines([SUBTITLE, MARKER], cyan_mask(image))
    assert kept == [SUBTITLE] and dropped == [MARKER]


def test_a_subtitle_on_a_light_background_is_kept() -> None:
    """A light, saturated beige scene behind the line: the earlier filter took it for
    letters of another colour and dropped whole subtitles (2026-10-23)."""
    image = scene(background=(150, 200, 230))
    kept, _ = colour_lines([SUBTITLE, MARKER], cyan_mask(image))
    assert kept == [SUBTITLE]


def test_pieces_of_one_row_are_judged_together() -> None:
    """The engine split the row: the piece with the red name alone has no cyan."""
    name = OcrLine("Ви:", 0.9, (5, 15, 55, 45))
    rest = OcrLine("Видишь, какая-то польза.", 0.9, (55, 15, 365, 45))
    image = scene()
    kept, dropped = colour_lines([name, rest, MARKER], cyan_mask(image))
    assert kept == [name, rest] and dropped == [MARKER]
    kept, _ = colour_lines([name, rest], cyan_mask(image), by_rows=False)
    assert kept == [rest]


def test_a_name_row_of_the_name_colour_stays_when_it_is_a_name() -> None:
    red = build_mask(
        scene(), MaskSpec(MaskPreset.COLOR, colors_hsv=(((0, 100, 100), (10, 255, 255)),))
    )
    name = OcrLine("[ Ви ]", 0.9, (5, 15, 55, 45))
    assert colour_lines([name], cyan_mask(scene()), red, lambda t: "[" in t)[0] == [name]
    assert colour_lines([name], cyan_mask(scene()), red, lambda t: False)[0] == []


def test_density_of_boxes() -> None:
    mask = np.zeros((10, 10), np.uint8)
    mask[0:5, 0:10] = 255
    assert density(mask, [(0, 0, 10, 10)]) == 0.5
    assert density(mask, [(20, 20, 30, 30)]) == 0.0  # out of the mask
