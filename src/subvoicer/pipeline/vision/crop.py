"""Cutting a zone out of a frame by its fractional rectangle."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from subvoicer.core.models import Rect


def pixel_box(rect: Rect, width: int, height: int) -> tuple[int, int, int, int]:
    """Convert a fractional rect to pixel ``(x1, y1, x2, y2)``, at least 1x1, inside the frame."""
    x1 = min(max(round(rect.x1 * width), 0), width - 1)
    y1 = min(max(round(rect.y1 * height), 0), height - 1)
    x2 = min(max(round(rect.x2 * width), x1 + 1), width)
    y2 = min(max(round(rect.y2 * height), y1 + 1), height)
    return x1, y1, x2, y2


def crop(image: npt.NDArray[np.uint8], rect: Rect) -> npt.NDArray[np.uint8]:
    """Return the zone of ``image`` (a view, not a copy)."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = pixel_box(rect, width, height)
    return image[y1:y2, x1:x2]
