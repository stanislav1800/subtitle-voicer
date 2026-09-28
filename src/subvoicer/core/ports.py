"""Ports: interfaces that engines implement and pipeline/services depend on (TZ 5.3).

Stage 0 holds draft ports; signatures are refined by the stages that implement them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

import numpy as np
import numpy.typing as npt

from subvoicer.core.events import Event
from subvoicer.core.models import AudioClip, FoundGame, Frame, OcrResult, Voice

E = TypeVar("E", bound=Event)


class Clock(Protocol):
    """Everything time-dependent goes through this port; tests use FakeClock."""

    def now(self) -> float:
        """Monotonic time in seconds."""
        ...


class FrameSource(Protocol):
    def start(self, hwnd: int) -> None: ...
    def latest(self) -> Frame | None: ...
    def stop(self) -> None: ...


class OcrEngine(Protocol):
    def recognize(self, image: npt.NDArray[np.uint8]) -> OcrResult: ...


class TtsEngine(Protocol):
    name: str

    def voices(self) -> list[Voice]: ...
    def synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip: ...


class StressMarker(Protocol):
    def mark(self, text: str) -> str:
        """Put ``+`` before stressed vowels, leaving words that already contain ``+`` intact."""
        ...


class Ducker(Protocol):
    def duck(self, pid: int) -> None: ...
    def restore(self, pid: int) -> None: ...
    def restore_all(self) -> None:
        """Called on any exit path."""
        ...


class GameLibrarySource(Protocol):
    name: str

    def scan(self) -> list[FoundGame]: ...


class TextSource(Protocol):
    """Where utterances come from: zone OCR, manual reading, later hooks."""

    def start(self) -> None: ...
    def stop(self) -> None: ...


class EventPublisher(Protocol):
    def publish(self, event: Event) -> None: ...


class EventBus(EventPublisher, Protocol):
    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None: ...
