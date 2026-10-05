"""Shared typed data models used by all layers and engines.

Models are added by the stages that need them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    """A captured frame in physical pixels and the monotonic time it was received.

    ``image`` is BGRA (WGC) or BGR (DXGI fallback); consumers go through ``to_bgr``.
    """

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
class TtsCapabilities:
    supports_rate: bool  # rate changes speed by the engine's own means (TZ 4.9)
    streaming: bool
    needs_gpu: bool


@dataclass(frozen=True, slots=True)
class Voice:
    id: str
    gender: Gender
    display_name: str
    preferred: bool = False  # better quality: first in lists and for assignment (TZ 1.19)


@dataclass(frozen=True, slots=True)
class AudioClip:
    samples: npt.NDArray[np.float32]  # mono
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate


@dataclass(frozen=True, slots=True)
class CaptureCapabilities:
    window_capture: bool  # captures the window itself (not what is on the monitor)


@dataclass(frozen=True, slots=True)
class WindowRef:
    """A top-level window; sizes are the client area in physical pixels."""

    hwnd: int
    pid: int
    exe: str
    title: str
    width: int
    height: int


class UtteranceKind(StrEnum):
    NEW = "new"
    CONTINUATION = "continuation"  # the tail of a line that kept growing (TZ 4.4, step 4)


@dataclass(frozen=True, slots=True)
class Utterance:
    """A line of text ready for voicing, from any source (zone OCR now, hooks later)."""

    channel: str  # zone id or hook channel
    kind: UtteranceKind
    text: str  # what to voice: without the speaker name; only the new tail for a continuation
    full_text: str  # the whole recognised line
    speaker: str | None
    t_change: float  # monotonic time the zone mask first changed (latency start, TZ 6)
    t_detected: float  # monotonic time the line was accepted
    confidence: float = 1.0
    ocr_input: str = ""
    id: int = 0  # unique per run; links the latency marks in the log (TZ 6)


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """A line to voice, as the speech scheduler sees it (TZ 4.9)."""

    id: int
    text: str  # prepared by TextPreparer later, right before synthesis
    voice_id: str
    priority: Priority
    continuation: bool = False  # queued right after the current line, never interrupts
    duck: bool = True
    channel: str = ""
    speaker: str | None = None
    t_change: float | None = None  # latency start, if the line came from a zone
    # extra dictionary entries (from, to, regex) for this line only: «Прослушать» before saving
    pronunciation: tuple[tuple[str, str, bool], ...] = ()
    voice_reason: str = ""  # why this voice (developer History, TZ 4.13.2)
    zone_name: str = ""


@dataclass(frozen=True, slots=True)
class FoundGame:
    """A game found by a library source (stage 9)."""

    title: str
    install_dir: Path
    exe: str | None
    icon_path: Path | None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """A recent line with its metrics (tray, developer History; TZ 4.13)."""

    id: int
    time: datetime
    channel: str
    speaker: str | None
    text: str  # as recognised, without the speaker name
    confidence: float = 1.0
    ocr_input: str = ""
    t_change: float | None = None
    voice: str = ""
    priority: str = ""
    rate: float = 1.0
    latency_ms: float | None = None  # TZ 6: mask change -> first sound
    outcome: str = "queued"  # queued | playing | done | cut | dropped:<reason>
    voice_reason: str = ""
    zone_name: str = ""
    prepared: str = ""  # after TextPreparer
