"""Events published on the bus. Services report facts here; data flows through queues."""

from __future__ import annotations

from dataclasses import dataclass

from subvoicer.core.models import SpeechRequest, Utterance, WindowRef


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
    prepared: str = ""  # the text after TextPreparer, as synthesized (developer History)


@dataclass(frozen=True, slots=True)
class SpeechFinished(Event):
    request: SpeechRequest
    completed: bool  # False: cut (new line, skip, pause, manual line)


@dataclass(frozen=True, slots=True)
class SpeechDropped(Event):
    request: SpeechRequest
    reason: str


@dataclass(frozen=True, slots=True)
class GameActivated(Event):
    """A game with a profile became the active one (TZ 4.11), or its profile file or window
    aspect changed and it has to be rebuilt."""

    profile_path: str
    window: WindowRef
    aspect: str  # layout key of the window's client area


@dataclass(frozen=True, slots=True)
class GameClosed(Event):
    """The active game's process ended."""

    pid: int


@dataclass(frozen=True, slots=True)
class LayoutMissing(Event):
    """The active profile has no zones: voicing does not run (TZ 1.26: one layout for all)."""

    profile_path: str
    title: str
    aspect: str


@dataclass(frozen=True, slots=True)
class ProfileOffered(Event):
    """«Создать профиль?» for a fullscreen window without a profile (TZ 4.11)."""

    window: WindowRef


@dataclass(frozen=True, slots=True)
class ProfileBound(Event):
    """A library profile without exe got the exe of a running game (TZ 4.11, rule 2)."""

    profile_path: str
    title: str


@dataclass(frozen=True, slots=True)
class LibraryScanned(Event):
    """Installed games were looked up (TZ 4.16); ``added`` — titles of new profiles."""

    added: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DebugCaptured(Event):
    """Ctrl+Alt+D saved a frame, zone crops and OCR results (TZ 4.13.2)."""

    folder: str


@dataclass(frozen=True, slots=True)
class SettingsApplied(Event):
    """The settings changed (window, tray switch or hotkey): views refresh themselves."""
