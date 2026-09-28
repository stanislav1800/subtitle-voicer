"""Shared typed data models used by all layers and engines.

Stage 0 contains only the models referenced by the ports in ``core/ports.py``;
the rest (Utterance, SpeechRequest, Mask, ...) are added by the stages that need them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import numpy.typing as npt


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class Priority(StrEnum):
    """Speech priority (TZ 4.9). ``MANUAL`` is for everything triggered by the user."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    MANUAL = "manual"


class ZoneTrigger(StrEnum):
    AUTO = "auto"
    HOTKEY = "hotkey"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class Rect:
    """Zone rectangle in fractions (0..1) of the capture frame: top-left and bottom-right."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        for value in (self.x1, self.y1, self.x2, self.y2):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Rect coordinates must be within 0..1, got {self}")
        if self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError(f"Rect must have positive width and height, got {self}")


@dataclass(frozen=True, slots=True)
class Frame:
    """A captured frame: BGRA pixels in physical pixels, and the monotonic receive time."""

    image: npt.NDArray[np.uint8]
    timestamp: float

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels of the OCR input image


@dataclass(frozen=True, slots=True)
class OcrResult:
    lines: tuple[OcrLine, ...]


@dataclass(frozen=True, slots=True)
class OcrCapabilities:
    languages: tuple[str, ...]
    returns_confidence: bool  # False -> every line reports confidence 1.0


@dataclass(frozen=True, slots=True)
class Voice:
    id: str
    gender: Gender
    display_name: str


@dataclass(frozen=True, slots=True)
class AudioClip:
    samples: npt.NDArray[np.float32]  # mono
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate


@dataclass(frozen=True, slots=True)
class FoundGame:
    """A game found by a library source (stage 9)."""

    title: str
    install_dir: Path
    exe: str | None
    icon_path: Path | None
