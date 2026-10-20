"""Subtitle plates on synthetic frames (TZ 4.2.2); real frames — tests/integration."""

from __future__ import annotations

import numpy as np

from subvoicer.pipeline.vision.plates import Box, find_plates, plate_mask, still_there

W, H = 1280, 360
STEP = 2  # the plate grows by 2 px


def near(a: Box, b: Box) -> bool:
    return all(abs(u - v) <= STEP for u, v in zip(a, b, strict=True))


def letters(image: np.ndarray, line: Box, value: int = 235) -> None:
    """Vertical strokes inside ``line``, none past its right edge."""
    x1, y1, x2, y2 = line
    for x in range(x1, x2 - 3, 9):
        image[y1:y2, x : x + 3] = value


def scene(seed: int = 1) -> np.ndarray:
    """A noisy game scene: no flat areas, like a real frame."""
    rng = np.random.default_rng(seed)
    return rng.integers(60, 140, (H, W, 3), dtype=np.uint8)


def draw_plate(image: np.ndarray, box: Box, colour=(12, 12, 12), letters=(235, 235, 235)) -> Box:
    """A plate with a line of 'letters' (vertical strokes) inside; returns the text line."""
    x1, y1, x2, y2 = box
    image[y1:y2, x1:x2] = colour
    pad = (y2 - y1) // 4
    line = (x1 + pad, y1 + pad, x2 - pad, y2 - pad)
    for x in range(line[0], line[2] - 3, 9):
        image[line[1] : line[3], x : x + 3] = letters
    return line


def test_a_plate_is_found_whole_from_its_text_line() -> None:
    image = scene()
    line = draw_plate(image, (440, 260, 840, 320))
    (plate,) = find_plates(image, [line])
    assert near(plate.box, (440, 260, 840, 320)) and plate.colour == (12, 12, 12)
    mask = plate_mask(image, [plate])
    assert mask[line[1] + 2, line[0] + 1] == 255  # a letter
    assert mask[262, 442] == 0 and mask[100, 100] == 0  # the plate itself, the scene
    assert still_there(image, plate)
    assert not still_there(scene(2), plate)  # the plate is gone


def test_text_without_a_plate_or_on_a_light_plate_is_not_a_subtitle() -> None:
    image = scene()
    hud = (100, 40, 400, 80)  # letters right on the scene
    letters(image, hud)
    answer = draw_plate(image, (440, 200, 840, 250), colour=(40, 200, 230), letters=(20, 20, 20))
    assert find_plates(image, [hud, answer]) == []


def test_two_plates_one_over_another_are_two_plates() -> None:
    image = scene()
    top = draw_plate(image, (540, 200, 740, 250))
    bottom = draw_plate(image, (340, 262, 940, 322))  # a strip of the scene between them
    plates = find_plates(image, [bottom, top])
    assert len(plates) == 2
    assert near(plates[0].box, (540, 200, 740, 250)) and near(plates[1].box, (340, 262, 940, 322))


def test_lines_of_one_plate_are_one_plate() -> None:
    image = scene()
    image[220:330, 340:940] = 12
    first, second = (360, 232, 920, 268), (420, 282, 860, 318)
    for line in (first, second):
        letters(image, line)
    (plate,) = find_plates(image, [first, second])
    assert near(plate.box, (340, 220, 940, 330)) and len(plate.lines) == 2


def test_a_plate_as_wide_as_the_screen_is_cut_to_its_text() -> None:
    """A letterbox bar: the 'plate' grows over the whole black strip."""
    image = scene()
    image[250:360] = 0
    line = (560, 280, 720, 320)
    letters(image, line)
    (plate,) = find_plates(image, [line])
    x1, y1, x2, y2 = plate.box
    assert x1 >= 540 and x2 <= 740 and y1 >= 260 and y2 <= 340


def test_a_menu_and_text_at_the_side_are_not_subtitles() -> None:
    image = scene()
    image[60:360, 300:980] = 0
    menu = [(500, 80 + 42 * i, 780, 110 + 42 * i) for i in range(4)]  # 4 lines, a menu
    for line in menu:
        letters(image, line, 200)
    assert find_plates(image, menu) == []
    image = scene()
    side = draw_plate(image, (20, 280, 320, 330))
    assert find_plates(image, [side]) == []
