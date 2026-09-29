"""Ports: interfaces that engines implement and pipeline/services depend on (TZ 5.3).

Stage 0 holds draft ports; signatures are refined by the stages that implement them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
    def start(self, hwnd: int) -> None:
        """Start capturing the window; raises on failure."""
        ...

    def latest(self) -> Frame | None:
        """The most recent frame (older ones are dropped), or None before the first one."""
        ...

    def is_alive(self) -> bool:
        """False once the capture session closed or failed and must be recreated."""
        ...

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


class AudioOutput(Protocol):
    """One line at a time; pieces of a line are appended while it plays."""

    def set_listeners(
        self,
        on_started: Callable[[int, float], None],
        on_finished: Callable[[int, bool], None],
    ) -> None:
        """``on_started(line_id, t)``: first sound; ``on_finished(line_id, completed)``."""
        ...

    def start_line(self, line_id: int, clip: AudioClip) -> None: ...
    def feed(self, line_id: int, clip: AudioClip, last: bool = False) -> None: ...
    def finish_feeding(self, line_id: int) -> None: ...
    def stop(self, line_id: int, fade_ms: int | None = None) -> None: ...


class HotkeyService(Protocol):
    """Global hotkeys in pynput format, e.g. ``<ctrl>+<alt>+p``."""

    def start(self, bindings: Mapping[str, Callable[[], None]]) -> None: ...
    def stop(self) -> None: ...
