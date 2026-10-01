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
