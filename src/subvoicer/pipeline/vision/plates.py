"""Subtitle plates in the search area of a «Субтитры (авто)» zone (TZ 4.2.2, stage 13).

Text lines come from a text detector (the caller's, RapidOCR's DBNet in the app); a line is
a subtitle when it sits on a plate: around it (or between its letters, for a tight plate)
there is mostly one colour, and the letters are lighter than that colour. The plate grows
from the line to its edges; lines one under another on touching plates are one plate. A
plate much bigger than its text (a letterbox bar, a dark scene of the same colour) is cut to
the text. Rules and thresholds were fitted on 58 real frames of three games
(``tests/fixtures/real/*/plates.json``, DECISIONS.md, stage 13).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from subvoicer.pipeline.vision.masks import Image

Box = tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels of the search image
CUT_PLATE = 0.6  # a strip at the plate's edge: this share in the plate colour...
CUT_LETTERS = 0.1  # ...and this share of letter pixels — a line of the plate is there


@dataclass(frozen=True, slots=True)
class PlateSettings:
    tolerance: int = 14  # per channel: still the plate colour
    ring_share: float = 0.5  # of the strip around a line in the plate colour
    inner_share: float = 0.45  # of a tight plate's line in the plate colour
    calm_gradient: int = 25  # pixels between the letters: a weaker local contrast
    min_calm: int = 50  # calm pixels needed to take the colour from inside the line
    letter_diff: int = 60  # a letter pixel differs from the plate colour by more
    grow_rows: float = 0.6  # a new row of the plate: this share in the plate colour
    grow_columns: float = 0.85  # a new column of the plate (a plate's side is straight)
    grow_step: int = 2
    line_gap: float = 0.6  # lines of one plate: the gap under this share of a line height
    max_lines: int = 3  # a pause menu has more
    centre: float = 0.15  # the text centre within this share of the width from the middle
    min_aspect: float = 2.0  # the text is wider than tall by this much


@dataclass(frozen=True, slots=True)
class Plate:
    box: Box
    colour: tuple[int, int, int]  # BGR
    lines: tuple[Box, ...]  # the text lines on it


def find_plates(
    image: Image, lines: Sequence[Box], settings: PlateSettings | None = None
) -> list[Plate]:
    """Plates of ``image`` (BGR) holding the detected text ``lines``, top to bottom."""
    s = settings or PlateSettings()
    bgr = np.ascontiguousarray(image[:, :, :3])
    width = bgr.shape[1]
    grown: list[tuple[Box, list[Box], npt.NDArray[np.int16]]] = []
    for line in sorted(_clip_all(lines, bgr.shape), key=lambda b: b[1]):
        colour = plate_colour(bgr, line, s)
        if colour is None:
            continue
        box = _grow(bgr, line, colour, s)
        for i, (other, members, first) in enumerate(grown):
            if _overlap(box, other) > 0 and any(_same_plate(line, m, s) for m in members):
                grown[i] = (_union([box, other]), [*members, line], first)
                break
        else:
            grown.append((box, [line], colour))
    plates: list[Plate] = []
    for box, members, colour in grown:
        if len(_rows(members)) > s.max_lines:
            continue
        text = _union(members)
        if not _centred(text, width, s):
            continue
        plate = Plate(
            _tighten(box, members, text),
            (int(colour[0]), int(colour[1]), int(colour[2])),
            tuple(members),
        )
        plates.append(plate)
    return sorted(_dedupe(plates), key=lambda p: p.box[1])


def plate_colour(
    image: Image, line: Box, settings: PlateSettings | None = None
) -> npt.NDArray[np.int16] | None:
    """The plate colour under a text line, or None if the line is not on a plate: around
    the line (a plate with margins, glowing letters) or between its letters (a tight plate,
    the scene right around it); the letters must be lighter than the plate."""
    s = settings or PlateSettings()
    height, width = image.shape[:2]
    x1, y1, x2, y2 = line
    pad = max(2, (y2 - y1) // 4)
    ring = image[max(0, y1 - pad) : min(height, y2 + pad), max(0, x1 - pad) : min(width, x2 + pad)]
    inner = image[y1:y2, x1:x2]
    if inner.size == 0:
        return None
    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    contrast = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    calm = inner[contrast < s.calm_gradient]
    candidates = [(ring, _median(ring), s.ring_share)]
    if len(calm) >= s.min_calm:
        candidates.append((inner, _median(calm), s.inner_share))
    pixels = inner.reshape(-1, 3).astype(np.int16)
    for region, guess, need in candidates:
        if _share(region, guess, s.tolerance) < need:
            continue
        letters = pixels[np.abs(pixels - guess).max(axis=1) > s.letter_diff]
        if len(letters) and letters.mean() > guess.mean():
            return guess
    return None


def plate_mask(
    image: Image, plates: Sequence[Plate], settings: PlateSettings | None = None
) -> Image:
    """The text mask of the search image: letter pixels inside the plates (far from the
    plate colour and lighter than it), nothing outside them."""
    s = settings or PlateSettings()
    mask = np.zeros(image.shape[:2], np.uint8)
    for plate in plates:
        x1, y1, x2, y2 = plate.box
        part = image[y1:y2, x1:x2, :3].astype(np.int16)
        colour = np.array(plate.colour, np.int16)
        far = np.abs(part - colour).max(axis=2) > s.letter_diff
        lighter = part.sum(axis=2) > int(colour.sum())
        mask[y1:y2, x1:x2] = np.where(far & lighter, 255, 0).astype(np.uint8)
    return mask


def cut_short(image: Image, plate: Plate, settings: PlateSettings | None = None) -> bool:
    """Right above or below the plate there are letters on the plate colour: a text line
    of this plate the detector missed (the plate stopped growing at it)."""
    s = settings or PlateSettings()
    height = image.shape[0]
    x1, y1, x2, y2 = plate.box
    reach = max(2, max(b[3] - b[1] for b in plate.lines) // 2)
    colour = np.array(plate.colour, np.int16)
    for top, bottom in ((max(0, y1 - reach), y1), (y2, min(height, y2 + reach))):
        if bottom <= top:
            continue
        part = image[top:bottom, x1:x2, :3].astype(np.int16)
        diff = np.abs(part - colour).max(axis=2)
        letters = (diff > s.letter_diff) & (part.sum(axis=2) > int(colour.sum()))
        if (diff <= s.tolerance).mean() >= CUT_PLATE and letters.mean() >= CUT_LETTERS:
            return True
    return False


def still_there(image: Image, plate: Plate, settings: PlateSettings | None = None) -> bool:
    """The plate's box still shows mostly its colour (else the plate is gone or moved)."""
    s = settings or PlateSettings()
    x1, y1, x2, y2 = plate.box
    colour = np.array(plate.colour, np.int16)
    return _share(image[y1:y2, x1:x2, :3], colour, s.tolerance) >= s.inner_share


# ---------------------------------------------------------------- internals


def _median(pixels: npt.NDArray[np.uint8]) -> npt.NDArray[np.int16]:
    return np.median(pixels.reshape(-1, 3), axis=0).astype(np.int16)


def _share(region: npt.NDArray[np.uint8], colour: npt.NDArray[np.int16], tolerance: int) -> float:
    if region.size == 0:
        return 0.0
    diff = np.abs(region.reshape(-1, 3).astype(np.int16) - colour).max(axis=1)
    return float((diff <= tolerance).mean())


def _grow(image: Image, line: Box, colour: npt.NDArray[np.int16], s: PlateSettings) -> Box:
    """The box grows while the next row / column of pixels is mostly the plate colour;
    shares come from an integral image of the colour match, so a step costs O(1)."""
    height, width = image.shape[:2]
    total = cv2.integral(_match(image, colour, s.tolerance))

    def share(ax1: int, ay1: int, ax2: int, ay2: int) -> float:
        area = (ax2 - ax1) * (ay2 - ay1)
        if area <= 0:
            return 0.0
        hit = total[ay2, ax2] - total[ay1, ax2] - total[ay2, ax1] + total[ay1, ax1]
        return float(hit) / area

    x1, y1, x2, y2 = line
    step = s.grow_step
    moved = True
    while moved:
        moved = False
        if y1 - step >= 0 and share(x1, y1 - step, x2, y1) >= s.grow_rows:
            y1 -= step
            moved = True
        if y2 + step <= height and share(x1, y2, x2, y2 + step) >= s.grow_rows:
            y2 += step
            moved = True
        if x1 - step >= 0 and share(x1 - step, y1, x1, y2) >= s.grow_columns:
            x1 -= step
            moved = True
        if x2 + step <= width and share(x2, y1, x2 + step, y2) >= s.grow_columns:
            x2 += step
            moved = True
    return x1, y1, x2, y2


def _match(image: Image, colour: npt.NDArray[np.int16], tolerance: int) -> Image:
    """1 where a pixel is the plate colour (every channel within ``tolerance``); OpenCV
    per-channel operations, ~10x faster than numpy on a 2560 px wide frame."""
    diff = cv2.absdiff(image, (float(colour[0]), float(colour[1]), float(colour[2]), 0.0))
    b, g, r = cv2.split(diff)
    _, match = cv2.threshold(cv2.max(cv2.max(b, g), r), tolerance, 1, cv2.THRESH_BINARY_INV)
    return match


def _tighten(box: Box, lines: list[Box], text: Box) -> Box:
    """A plate much bigger than its text is cut to the text plus a margin; a real plate
    (a little bigger than its text) stays as it is."""
    line_height = max(b[3] - b[1] for b in _rows(lines))
    if (box[2] - box[0]) <= (text[2] - text[0]) + 3 * line_height and (box[3] - box[1]) <= (
        text[3] - text[1]
    ) + 2 * line_height:
        return box
    pad = max(10, int(line_height * 0.35))
    return (
        max(box[0], text[0] - pad),
        max(box[1], text[1] - pad),
        min(box[2], text[2] + pad),
        min(box[3], text[3] + pad),
    )


def _same_plate(a: Box, b: Box, s: PlateSettings) -> bool:
    """Two lines of one plate: one right under the other, overlapping horizontally."""
    line_height = max(a[3] - a[1], b[3] - b[1])
    gap = max(a[1], b[1]) - min(a[3], b[3])
    return gap < s.line_gap * line_height and min(a[2], b[2]) > max(a[0], b[0])


def _rows(lines: list[Box]) -> list[Box]:
    rows: list[Box] = []
    for line in sorted(lines, key=lambda b: b[1]):
        for i, row in enumerate(rows):
            common = min(line[3], row[3]) - max(line[1], row[1])
            if common > 0.5 * min(line[3] - line[1], row[3] - row[1]):
                rows[i] = _union([row, line])
                break
        else:
            rows.append(line)
    return rows


def _centred(text: Box, width: int, s: PlateSettings) -> bool:
    centre = (text[0] + text[2]) / 2
    return abs(centre - width / 2) <= s.centre * width and (text[2] - text[0]) >= s.min_aspect * (
        text[3] - text[1]
    )


def _dedupe(plates: list[Plate]) -> list[Plate]:
    """A plate mostly inside a bigger one (one plate found from two of its lines) goes."""
    keep: list[Plate] = []
    for plate in sorted(plates, key=lambda p: -_area(p.box)):
        if not any(_overlap(plate.box, k.box) >= 0.6 * _area(plate.box) for k in keep):
            keep.append(plate)
    return keep


def _clip_all(lines: Sequence[Box], shape: tuple[int, ...]) -> list[Box]:
    height, width = shape[:2]
    clipped = []
    for x1, y1, x2, y2 in lines:
        box = (max(0, x1), max(0, y1), min(width, x2), min(height, y2))
        if box[2] > box[0] and box[3] > box[1]:
            clipped.append(box)
    return clipped


def _area(box: Box) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _overlap(a: Box, b: Box) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _union(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
