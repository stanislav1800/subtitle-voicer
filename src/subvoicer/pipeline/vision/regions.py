"""Splitting a zone into the speaker name and the text by the name colour (TZ 4.5, 1.36)."""

from __future__ import annotations

import cv2
import numpy as np

from subvoicer.pipeline.vision.masks import Image, u8

WORD_KERNEL = (9, 5)  # joins the letters of a name into word blobs
PAD = 4


def grow(mask: Image, kernel: tuple[int, int] = WORD_KERNEL) -> Image:
    return u8(cv2.dilate(mask, np.ones((kernel[1], kernel[0]), np.uint8)))


def without(mask: Image, other: Image) -> Image:
    """``mask`` minus the (grown) pixels of ``other``: the text mask without the name."""
    return u8(cv2.bitwise_and(mask, cv2.bitwise_not(grow(other, (3, 3)))))


def word_boxes(mask: Image) -> list[tuple[int, int, int, int]]:
    """Bounding boxes ``x1, y1, x2, y2`` of the words of a mask."""
    count, _, stats, _ = cv2.connectedComponentsWithStats(grow(mask), connectivity=8)
    boxes = []
    for i in range(1, count):
        x, y, w, h = (int(v) for v in stats[i, :4])
        boxes.append((x, y, x + w, y + h))
    return boxes


def paint_out(image: Image, mask: Image) -> Image:
    """A copy of ``image`` with the words of ``mask`` painted black (padded), so OCR of the
    text does not read the name."""
    result = image.copy()
    height, width = mask.shape[:2]
    for x1, y1, x2, y2 in word_boxes(mask):
        result[
            max(0, y1 - PAD) : min(height, y2 + PAD), max(0, x1 - PAD) : min(width, x2 + PAD)
        ] = 0
    return result


def cut_out(image: Image, mask: Image) -> Image | None:
    """The part of ``image`` holding the pixels of ``mask`` (padded), or None if it is empty."""
    points = cv2.findNonZero(mask)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    height, width = mask.shape[:2]
    return image[
        max(0, y - PAD) : min(height, y + h + PAD), max(0, x - PAD) : min(width, x + w + PAD)
    ]


NAME_REACH = 14  # a name ends within this many text-line heights left of the text start
NAME_MIN_HEIGHT = 96  # a smaller name cut-out is enlarged: OCR misses short ones («Ви:»)


Box = tuple[int, int, int, int]  # x1, y1, x2, y2


def first_line(text_mask: Image) -> Box | None:
    """The box of the first text line of a mask: its rows with no gap, from where it starts."""
    rows = np.flatnonzero(text_mask.any(axis=1))
    if len(rows) == 0:
        return None
    top = bottom = int(rows[0])
    for row in rows[1:]:  # the first line: rows of text with no gap
        if row - bottom > 3:
            break
        bottom = int(row)
    cols = np.flatnonzero(text_mask[top : bottom + 1].any(axis=0))
    return int(cols[0]), top, int(cols[-1]) + 1, bottom + 1


def name_beside_text(image: Image, name_mask: Image, text_mask: Image) -> Image | None:
    """``name_before`` the first text line of a mask."""
    line = first_line(text_mask)
    return name_before(image, name_mask, line) if line is not None else None


def name_before(image: Image, name_mask: Image, line: Box) -> Image | None:
    """The name in front of a text line: name-colour pixels on that line's rows, left of
    where the text starts. Pixels of the name colour elsewhere in the zone (neon of the scene,
    a red HUD label in Cyberpunk) stretched the cut-out over the background and the text, and
    the name came out empty or with words of the line («Пациентка: Не зн»). None — nothing of
    the name colour before it."""
    start, top, _end, bottom = line
    height = max(1, bottom - top)
    y1, y2 = max(0, top - height // 3), min(image.shape[0], bottom + height // 3)
    x1, x2 = max(0, start - NAME_REACH * height), start + height // 2
    return _part(image, name_mask, y1, y2, x1, x2)


def name_above(image: Image, name_mask: Image, line: Box) -> Image | None:
    """The name on its own row right above a text line (TZ 4.5, 1.36): name-colour pixels
    within 1.5 line heights above it, over the line's width and a little more, then the whole
    of that row (a tall name is not cut at the top). A HUD label of the same colour further up,
    past a gap, is not the name."""
    start, top, end, bottom = line
    height = max(1, bottom - top)
    y1, y2 = max(0, top - height * 3 // 2), max(0, top - 1)
    x1, x2 = max(0, start - 2 * height), min(image.shape[1], end + 2 * height)
    if y2 <= y1 or x2 <= x1:
        return None
    band = name_mask[:y2, x1:x2]
    rows = np.flatnonzero(band[y1:].any(axis=1))
    if len(rows) == 0:
        return None
    lowest = y1 + int(rows[-1])
    filled = band.any(axis=1)
    highest = lowest
    while highest > 0 and (filled[highest - 1] or filled[max(0, highest - 3) : highest].any()):
        highest -= 1  # up the name's row, over gaps of up to 3 rows inside the letters
    return _part(image, name_mask, highest, lowest + 1, x1, x2)


def _part(image: Image, name_mask: Image, y1: int, y2: int, x1: int, x2: int) -> Image | None:
    if y2 <= y1 or x2 <= x1:
        return None
    points = cv2.findNonZero(name_mask[y1:y2, x1:x2])
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    return image[
        max(0, y1 + y - PAD) : min(image.shape[0], y1 + y + h + PAD),
        max(0, x1 + x - PAD) : min(image.shape[1], x1 + x + w + PAD),
    ]


def enlarged(image: Image, min_height: int = NAME_MIN_HEIGHT) -> Image:
    """A small cut-out scaled up (up to 3x) so OCR reads a two-letter name."""
    height = image.shape[0]
    if height == 0 or height >= min_height:
        return image
    factor = min(3.0, min_height / height)
    return cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
