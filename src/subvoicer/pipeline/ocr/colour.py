"""Lines of the zone's colour (TZ 4.3, 1.35): the whole crop is read (the best input by the
stage 1 measure), then lines whose letters are mostly of another colour go — a yellow answer
option or a yellow «15M» under a cyan subtitle in Cyberpunk 2077."""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from subvoicer.core.models import OcrLine
from subvoicer.pipeline.vision.masks import Image, to_bgr

MIN_SHARE = 0.5  # subtitles 0.79-0.99, other colours 0-0.17 (two Cyberpunk frames, 2026-10-23)
MIN_PIXELS = 20  # fewer letter pixels than that: not enough to judge, the line stays
# letter pixels of any colour: bright and saturated (a coloured letter), or of the mask
LETTER_SATURATION, LETTER_VALUE = 80, 140


def colour_share(image: Image, mask: Image, bbox: tuple[int, int, int, int]) -> float | None:
    """Share of the zone-colour pixels among the letter pixels in ``bbox``; None if too few."""
    height, width = mask.shape[:2]
    x1, y1, x2, y2 = bbox
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    hsv = cv2.cvtColor(to_bgr(image[y1:y2, x1:x2]), cv2.COLOR_BGR2HSV)
    inside = mask[y1:y2, x1:x2] > 0
    coloured = (hsv[..., 1] > LETTER_SATURATION) & (hsv[..., 2] > LETTER_VALUE)
    letters = int(np.count_nonzero(coloured | inside))
    if letters < MIN_PIXELS:
        return None
    return int(np.count_nonzero(inside)) / letters


def colour_lines(
    lines: Sequence[OcrLine], image: Image, mask: Image
) -> tuple[list[OcrLine], list[OcrLine]]:
    """``lines`` (bboxes in ``image`` pixels) split into the kept and the dropped ones."""
    kept, dropped = [], []
    for line in lines:
        share = colour_share(image, mask, line.bbox)
        (kept if share is None or share >= MIN_SHARE else dropped).append(line)
    return kept, dropped
