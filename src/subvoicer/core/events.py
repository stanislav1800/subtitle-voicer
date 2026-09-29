"""Events published on the bus. Services report facts here; data flows through queues."""

from __future__ import annotations

from dataclasses import dataclass

from subvoicer.core.models import SpeechRequest, Utterance


@dataclass(frozen=True, slots=True)
class Event:
    """Base class of all bus events. Subscribing to a base class receives its subclasses."""


@dataclass(frozen=True, slots=True)
class EngineUnavailable(Event):
    """No engine of a family could be loaded (the whole fallback chain failed)."""

    family: str
    engine_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class UtteranceDetected(Event):
    utterance: Utterance


@dataclass(frozen=True, slots=True)
class CaptureFailed(Event):
    """Capture could not be restored after the allowed attempts; retries continue slower."""

    hwnd: int
    attempts: int


@dataclass(frozen=True, slots=True)
class CaptureRestored(Event):
    hwnd: int


@dataclass(frozen=True, slots=True)
class CaptureStopped(Event):
    """The captured window is gone (the game was closed)."""

    hwnd: int


@dataclass(frozen=True, slots=True)
class CaptureModeChanged(Event):
    hwnd: int
    mode: str  # "wgc" | "dxgi"


@dataclass(frozen=True, slots=True)
class SpeechStarted(Event):
    request: SpeechRequest
    rate: float
    t_play: float  # monotonic time the first audio buffer went out (latency end, TZ 6)


@dataclass(frozen=True, slots=True)
class SpeechFinished(Event):
    request: SpeechRequest
    completed: bool  # False: cut (new line, skip, pause, manual line)


@dataclass(frozen=True, slots=True)
class SpeechDropped(Event):
    request: SpeechRequest
    reason: str
