"""The three OCR input variants (TZ 4.3) and upscaling of small text."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
import numpy.typing as npt

from subvoicer.pipeline.vision.masks import to_bgr, u8

Image = npt.NDArray[np.uint8]

MASK_DILATION_PX = 2
MIN_RUN_HEIGHT_PX = 3  # rows of mask noise thinner than this are not text lines


class OcrInput(StrEnum):
    RAW = "raw"  # (a) the zone crop as is
    MASKED = "masked"  # (b) crop with everything outside the (dilated) mask painted black
    MASK = "mask"  # (c) the mask itself: black text on white


@dataclass(frozen=True, slots=True)
class PreparedInput:
    image: Image
    scale: float  # input pixels per crop pixel


def build_input(
    crop: Image,
    mask: Image,
    kind: OcrInput,
    *,
    upscale: float = 2.0,
    min_line_height: int = 20,
) -> PreparedInput:
    """Build an OCR input from a zone crop and its text mask.

    The image is enlarged by ``upscale`` (INTER_CUBIC) when the text line height estimated
    from the mask is below ``min_line_height``.
    """
    match kind:
        case OcrInput.RAW:
            image = to_bgr(crop)
        case OcrInput.MASKED:
            size = 2 * MASK_DILATION_PX + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
            keep = cv2.dilate(mask, kernel) > 0
            image = u8(np.where(keep[:, :, None], to_bgr(crop), 0).astype(np.uint8))
        case OcrInput.MASK:
            image = u8(cv2.bitwise_not(mask))

    line_height = estimate_line_height(mask)
    if upscale > 1.0 and line_height is not None and line_height < min_line_height:
        image = u8(cv2.resize(image, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC))
        return PreparedInput(image, upscale)
    return PreparedInput(image, 1.0)


def estimate_line_height(mask: Image) -> int | None:
    """Median height of horizontal bands of text in the mask; ``None`` for an empty mask."""
    rows = np.count_nonzero(mask, axis=1) > 0
    heights: list[int] = []
    run = 0
    for filled in rows:
        if filled:
            run += 1
        elif run:
            heights.append(run)
            run = 0
    if run:
        heights.append(run)
    heights = [h for h in heights if h >= MIN_RUN_HEIGHT_PX]
    if not heights:
        return None
    return int(np.median(heights))
