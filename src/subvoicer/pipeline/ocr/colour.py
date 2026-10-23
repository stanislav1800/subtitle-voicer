"""Lines of the zone's colour (TZ 4.3, 1.35, 1.37): the whole crop is read (the best input by
the stage 1 measure), then rows with few pixels of the zone's colour mask go — a yellow answer
option or a yellow «15M» under a cyan subtitle in Cyberpunk 2077.

The measure is the density of the colour mask in the row's box. By 42 frames of a Cyberpunk
letsplay, light and dark (DECISIONS 2026-10-23): subtitle rows 0.11-0.24, rows of other
colours 0.00-0.02. The share of the zone's hue among «letter» pixels (bright and saturated)
it replaced counted a light background as letters and dropped whole subtitles of light
frames (5 of 41 read empty)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from subvoicer.core.models import OcrLine
from subvoicer.pipeline.vision.masks import Image

MIN_DENSITY = 0.06  # of the row box: subtitles >= 0.11, other colours <= 0.02
SAME_ROW_OVERLAP = 0.5
Box = tuple[int, int, int, int]


def density(mask: Image, boxes: Sequence[Box]) -> float:
    """Share of the mask pixels in ``boxes`` (clipped to the mask); 0 for empty boxes."""
    height, width = mask.shape[:2]
    filled = area = 0
    for x1, y1, x2, y2 in boxes:
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        filled += int(np.count_nonzero(mask[y1:y2, x1:x2]))
        area += (x2 - x1) * (y2 - y1)
    return filled / area if area else 0.0


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
    mask: Image,
    names: Image | None = None,
    named: Callable[[str], bool] | None = None,
    *,
    by_rows: bool = True,
) -> tuple[list[OcrLine], list[OcrLine]]:
    """``lines`` (bboxes in ``mask`` pixels, reading order) split into the kept and the
    dropped ones, a row at a time: a row stays if the colour ``mask`` is dense enough in it.
    A row of the name colour mask ``names`` that ``named`` takes for a name stays too: it is
    the speaker's name («[ П-3 ]» over the line, TZ 1.36), while a HUD label of that colour
    is not. ``by_rows`` False judges every piece on its own: a name and something else beside
    it in a name zone."""
    dropped: set[int] = set()
    for row in rows_of(lines) if by_rows else [[line] for line in lines]:
        boxes = [line.bbox for line in row]
        if density(mask, boxes) >= MIN_DENSITY:
            continue
        text = " ".join(line.text for line in row)
        is_name = names is not None and named is not None and named(text)
        if not (is_name and density(names, boxes) >= MIN_DENSITY):
            dropped |= {id(line) for line in row}
    kept = [line for line in lines if id(line) not in dropped]
    return kept, [line for line in lines if id(line) in dropped]
