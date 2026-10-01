"""Aspect ratio key of a capture frame (TZ 4.11): zones are kept per key."""

from __future__ import annotations

KNOWN = {"16:9": 16 / 9, "16:10": 16 / 10, "21:9": 21 / 9, "32:9": 32 / 9, "4:3": 4 / 3}
TOLERANCE = 0.03


def aspect_key(width: int, height: int) -> str:
    """The nearest known ratio, or ``"WxH"`` when even the nearest is more than 3% off."""
    if width <= 0 or height <= 0:
        raise ValueError(f"Bad frame size {width}x{height}")
    ratio = width / height
    key, value = min(KNOWN.items(), key=lambda item: abs(ratio - item[1]) / item[1])
    if abs(ratio - value) / value > TOLERANCE:
        return f"{width}x{height}"
    return key
