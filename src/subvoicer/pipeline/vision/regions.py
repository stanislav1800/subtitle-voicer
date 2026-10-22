"""Splitting a zone into the speaker name and the text by colour (TZ 4.5, mode ``color``)."""

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


def name_beside_text(image: Image, name_mask: Image, text_mask: Image) -> Image | None:
    """The name in front of the first text line: name-colour pixels on that line's rows,
    left of where the text starts. Pixels of the name colour elsewhere in the zone (neon of
    the scene in Cyberpunk) stretched the cut-out over the background and the text, and the
    name came out empty or with words of the line («Пациентка: Не зн»). None — no text, or
    nothing of the name colour before it."""
    rows = np.flatnonzero(text_mask.any(axis=1))
    if len(rows) == 0:
        return None
    top = bottom = int(rows[0])
    for row in rows[1:]:  # the first line: rows of text with no gap
        if row - bottom > 3:
            break
        bottom = int(row)
    height = bottom - top + 1
    start = int(np.flatnonzero(text_mask[top : bottom + 1].any(axis=0))[0])
    y1, y2 = max(0, top - height // 3), min(image.shape[0], bottom + 1 + height // 3)
    x1, x2 = max(0, start - NAME_REACH * height), start + height // 2
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
