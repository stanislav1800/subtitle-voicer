"""Lines of the zone's colour (TZ 4.3, 1.35): the whole crop is read (the best input by the
stage 1 measure), then rows whose letters are mostly of another hue go — a yellow answer
option or a yellow «15M» under a cyan subtitle in Cyberpunk 2077."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import cv2
import numpy as np

from subvoicer.core.models import OcrLine
from subvoicer.pipeline.vision.masks import HsvRange, Image, to_bgr

MIN_SHARE = 0.5  # subtitles 0.79-0.99, other colours 0-0.17 (two Cyberpunk frames, 2026-10-23)
MIN_PIXELS = 20  # fewer letter pixels than that: not enough to judge, the row stays
# letter pixels: saturated and not dark. The hue is compared, not the whole range: a line
# fading in or out is dimmer than the range's lowest V, yet of the same hue — judged by the
# range, pieces of fading subtitles were dropped («квортолов от дома.», 2026-10-23)
LETTER_SATURATION, LETTER_VALUE = 60, 90
SAME_ROW_OVERLAP = 0.5


def judged(ranges: Sequence[HsvRange]) -> bool:
    """A colour of grey or white letters (low saturation) has no hue to tell rows apart."""
    return bool(ranges) and all(high[1] >= LETTER_SATURATION for _low, high in ranges)


def colour_share(
    image: Image, ranges: Sequence[HsvRange], boxes: Sequence[tuple[int, int, int, int]]
) -> float | None:
    """Share of the letter pixels in ``boxes`` whose hue is the zone's; None if too few."""
    height, width = image.shape[:2]
    letters = ours = 0
    for x1, y1, x2, y2 in boxes:
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        hsv = cv2.cvtColor(to_bgr(image[y1:y2, x1:x2]), cv2.COLOR_BGR2HSV)
        letter = (hsv[..., 1] > LETTER_SATURATION) & (hsv[..., 2] > LETTER_VALUE)
        hue = hsv[..., 0]
        in_hue = np.zeros_like(letter)
        for (h1, _s1, _v1), (h2, _s2, _v2) in ranges:
            in_hue |= (hue >= h1) & (hue <= h2)
        letters += int(np.count_nonzero(letter))
        ours += int(np.count_nonzero(letter & in_hue))
    return ours / letters if letters >= MIN_PIXELS else None


def rows_of(lines: Sequence[OcrLine]) -> list[list[OcrLine]]:
    """Lines (pieces of one row, as the engine split it) grouped into rows."""
    rows: list[list[OcrLine]] = []
    for line in lines:
        for row in rows:
            top = min(ln.bbox[1] for ln in row)
            bottom = max(ln.bbox[3] for ln in row)
            overlap = min(bottom, line.bbox[3]) - max(top, line.bbox[1])
            if overlap >= SAME_ROW_OVERLAP * min(bottom - top, line.bbox[3] - line.bbox[1]):
                row.append(line)
                break
        else:
            rows.append([line])
    return rows


def colour_lines(
    lines: Sequence[OcrLine],
    image: Image,
    ranges: Sequence[HsvRange],
    names: Sequence[HsvRange] = (),
    named: Callable[[str], bool] | None = None,
    *,
    by_rows: bool = True,
) -> tuple[list[OcrLine], list[OcrLine]]:
    """``lines`` (bboxes in ``image`` pixels, reading order) split into the kept and the
    dropped ones, a row at a time. A row of the name colour ``names`` that ``named`` takes for
    a name is kept too: it is the speaker's name («[ П-3 ]» over the line, TZ 1.36), while a
    HUD label of that colour is not. ``by_rows`` False judges every piece on its own: a name
    and something else beside it in a name zone."""
    if not judged(ranges):
        return list(lines), []
    dropped: set[int] = set()
    for row in rows_of(lines) if by_rows else [[line] for line in lines]:
        boxes = [line.bbox for line in row]
        share = colour_share(image, ranges, boxes)
        if share is None or share >= MIN_SHARE:
            continue
        text = " ".join(line.text for line in row)
        is_name = named is not None and named(text) and judged(names)
        name = colour_share(image, names, boxes) if is_name else None
        if name is None or name < MIN_SHARE:
            dropped |= {id(line) for line in row}
    kept = [line for line in lines if id(line) not in dropped]
    return kept, [line for line in lines if id(line) in dropped]
