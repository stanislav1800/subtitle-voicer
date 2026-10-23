import numpy as np

from subvoicer.core.models import OcrLine
from subvoicer.pipeline.ocr.colour import colour_lines, colour_share

CYAN = ((80, 90, 170), (100, 255, 255))
BGR_CYAN, BGR_YELLOW = (230, 210, 40), (40, 210, 240)


def scene(cyan_scale: float = 1.0) -> np.ndarray:
    """A dark frame: a cyan subtitle line with a red name, a yellow «15M» below."""
    image = np.full((120, 400, 3), 25, dtype=np.uint8)
    cyan = tuple(int(c * cyan_scale) for c in BGR_CYAN)
    for x in range(60, 360, 8):
        image[20:40, x : x + 4] = cyan
    for x in range(10, 50, 8):
        image[20:40, x : x + 4] = (40, 40, 230)  # the name «Ви:» in red
    for x in range(180, 220, 8):
        image[80:95, x : x + 4] = BGR_YELLOW
    return image


SUBTITLE = OcrLine("Ви: Видишь, какая-то польза от тебя.", 0.95, (5, 15, 365, 45))
MARKER = OcrLine("15M", 0.9, (175, 75, 225, 100))


def test_lines_of_another_colour_are_dropped() -> None:
    kept, dropped = colour_lines([SUBTITLE, MARKER], scene(), [CYAN])
    assert kept == [SUBTITLE] and dropped == [MARKER]


def test_a_fading_subtitle_is_kept() -> None:
    """Dimmer than the range's lowest V (170): the hue is the zone's (2026-10-23)."""
    kept, _ = colour_lines([SUBTITLE, MARKER], scene(cyan_scale=0.6), [CYAN])
    assert kept == [SUBTITLE]


def test_pieces_of_one_row_are_judged_together() -> None:
    """The engine split the row: the piece with the red name alone is not of the colour."""
    name = OcrLine("Ви:", 0.9, (5, 15, 55, 45))
    rest = OcrLine("Видишь, какая-то польза.", 0.9, (55, 15, 365, 45))
    kept, dropped = colour_lines([name, rest, MARKER], scene(), [CYAN])
    assert kept == [name, rest] and dropped == [MARKER]


def test_too_few_letters_or_a_grey_colour_are_not_judged() -> None:
    assert colour_share(scene(), [CYAN], [(370, 100, 380, 110)]) is None
    assert colour_share(scene(), [CYAN], [(500, 0, 600, 10)]) is None  # out of the crop
    white = ((0, 0, 200), (179, 40, 255))
    assert colour_lines([SUBTITLE, MARKER], scene(), [white]) == ([SUBTITLE, MARKER], [])
