"""Synthetic zone images for vision/OCR tests, drawn without system fonts."""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

Image = npt.NDArray[np.uint8]

ZONE_W, ZONE_H = 600, 80


def draw_text(
    image: Image, text: str, color_bgr: tuple[int, int, int], outline: bool = True
) -> Image:
    out = image.copy()
    org = (20, 55)
    font = cv2.FONT_HERSHEY_SIMPLEX
    if outline:
        cv2.putText(out, text, org, font, 1.4, (0, 0, 0), 7, cv2.LINE_AA)
    cv2.putText(out, text, org, font, 1.4, color_bgr, 3, cv2.LINE_AA)
    return out


def dark_noise(rng: np.random.Generator, width: int = ZONE_W * 2) -> Image:
    return rng.integers(0, 90, size=(ZONE_H, width, 3), dtype=np.uint8)


def motley(rng: np.random.Generator, width: int = ZONE_W * 2) -> Image:
    """Coloured blobs, all darker than V=150 so they never look like bright text."""
    image = np.zeros((ZONE_H, width, 3), dtype=np.uint8)
    for _ in range(60):
        x, y = int(rng.integers(0, width)), int(rng.integers(0, ZONE_H))
        color = tuple(int(c) for c in rng.integers(0, 150, size=3))
        cv2.circle(image, (x, y), int(rng.integers(5, 30)), color, -1)
    return image
