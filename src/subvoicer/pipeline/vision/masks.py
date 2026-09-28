"""Text mask presets (TZ 4.2.1): a binary image (0 / 255) where only text pixels remain."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt

Image = npt.NDArray[np.uint8]
Hsv = tuple[int, int, int]
HsvRange = tuple[Hsv, Hsv]  # inclusive (low, high), OpenCV scale: H 0-179, S 0-255, V 0-255

H_MAX = 179
SV_MAX = 255


class MaskPreset(StrEnum):
    COLOR = "color"
    BRIGHT_ON_DARK = "bright_on_dark"
    DARK_ON_BRIGHT = "dark_on_bright"
    ADAPTIVE = "adaptive"


DEFAULT_THRESHOLDS: dict[MaskPreset, int] = {
    MaskPreset.BRIGHT_ON_DARK: 200,
    MaskPreset.DARK_ON_BRIGHT: 60,
}


@dataclass(frozen=True, slots=True)
class MaskSpec:
    preset: MaskPreset
    colors_hsv: tuple[HsvRange, ...] = ()
    threshold: int | None = None  # None -> DEFAULT_THRESHOLDS[preset]
    close_kernel: int = 3  # morphological closing for bright/dark presets
    adaptive_block: int = 31  # odd neighbourhood size for cv2.adaptiveThreshold
    adaptive_c: int = 15
    min_component_ratio: float = 0.0002  # components smaller than 0.02% of the zone are noise

    def __post_init__(self) -> None:
        if self.preset is MaskPreset.COLOR and not self.colors_hsv:
            raise ValueError("Preset 'color' needs at least one HSV range")


def u8(array: Any) -> Image:
    """Type OpenCV output as a uint8 image (cv2 stubs return untyped arrays)."""
    return cast(Image, array)


def to_bgr(image: Image) -> Image:
    """Accept BGRA (capture frames), BGR or grayscale; return BGR."""
    if image.ndim == 2:
        return u8(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR))
    if image.shape[2] == 4:
        return u8(cv2.cvtColor(image, cv2.COLOR_BGRA2BGR))
    return image


def brightness(image: Image) -> Image:
    """HSV value: max(B, G, R). Coloured text (yellow, turquoise) counts as bright too."""
    if image.ndim == 2:
        return image
    result: Image = np.max(image[:, :, :3], axis=2)
    return result


def build_mask(image: Image, spec: MaskSpec) -> Image:
    """Build a text mask for a zone crop (BGRA, BGR or gray)."""
    match spec.preset:
        case MaskPreset.COLOR:
            mask = _color_mask(to_bgr(image), spec.colors_hsv)
        case MaskPreset.BRIGHT_ON_DARK | MaskPreset.DARK_ON_BRIGHT:
            threshold = spec.threshold
            if threshold is None:
                threshold = DEFAULT_THRESHOLDS[spec.preset]
            value = brightness(image)
            if spec.preset is MaskPreset.BRIGHT_ON_DARK:
                mask = np.where(value >= threshold, 255, 0).astype(np.uint8)
            else:
                mask = np.where(value <= threshold, 255, 0).astype(np.uint8)
            mask = _close(mask, spec.close_kernel)
        case MaskPreset.ADAPTIVE:
            mask = _adaptive_mask(image, spec.adaptive_block, spec.adaptive_c)
    min_area = spec.min_component_ratio * mask.shape[0] * mask.shape[1]
    return remove_small_components(mask, min_area)


def remove_small_components(mask: Image, min_area: float) -> Image:
    """Drop 8-connected white components with area below ``min_area`` pixels."""
    if min_area <= 1 or not mask.any():
        return mask
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= min_area
    keep[0] = False  # background
    result: Image = np.where(keep[labels], 255, 0).astype(np.uint8)
    return result


def hsv_ranges_around(color_hsv: Hsv, dh: int = 10, ds: int = 80, dv: int = 80) -> list[HsvRange]:
    """Ranges covering ``color ± (dh, ds, dv)``; hue wraps around, so it may yield two ranges."""
    h, s, v = color_hsv
    s_lo, s_hi = max(s - ds, 0), min(s + ds, SV_MAX)
    v_lo, v_hi = max(v - dv, 0), min(v + dv, SV_MAX)
    lo, hi = h - dh, h + dh
    if lo < 0:
        return [
            ((0, s_lo, v_lo), (hi, s_hi, v_hi)),
            ((H_MAX + 1 + lo, s_lo, v_lo), (H_MAX, s_hi, v_hi)),
        ]
    if hi > H_MAX:
        return [
            ((lo, s_lo, v_lo), (H_MAX, s_hi, v_hi)),
            ((0, s_lo, v_lo), (hi - H_MAX - 1, s_hi, v_hi)),
        ]
    return [((lo, s_lo, v_lo), (hi, s_hi, v_hi))]


def bgr_to_hsv(color_bgr: tuple[int, int, int]) -> Hsv:
    pixel = np.array([[color_bgr]], dtype=np.uint8)
    h, s, v = cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0, 0]
    return int(h), int(s), int(v)


def _color_mask(bgr: Image, ranges: Sequence[HsvRange]) -> Image:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
    for low, high in ranges:
        mask |= u8(cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8)))
    return mask


def _close(mask: Image, size: int) -> Image:
    if size <= 1:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
    return u8(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel))


def _adaptive_mask(image: Image, block: int, c: int) -> Image:
    """Adaptive threshold; polarity is chosen so that text is the minority class (Otsu split)."""
    gray = image if image.ndim == 2 else cv2.cvtColor(to_bgr(image), cv2.COLOR_BGR2GRAY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright_is_minority = np.count_nonzero(otsu) < otsu.size / 2
    if bright_is_minority:  # light text: pixels brighter than the local mean + c
        mode, offset = cv2.THRESH_BINARY, -c
    else:  # dark text: pixels darker than the local mean - c
        mode, offset = cv2.THRESH_BINARY_INV, c
    return u8(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, mode, block, offset))
