"""Fake FrameSource and a recording event bus."""

from __future__ import annotations

import numpy as np

from subvoicer.core.events import Event
from subvoicer.core.models import Frame


def solid_frame(value: int, timestamp: float, size: tuple[int, int] = (40, 60)) -> Frame:
    image = np.full((*size, 4), value, dtype=np.uint8)
    image[:, :, 3] = 255
    return Frame(image, timestamp)


class FakeFrameSource:
    """Frames are pushed by the test; ``fail_start`` makes ``start`` raise."""

    def __init__(self, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.frame: Frame | None = None
        self.alive = False
        self.started_hwnd: int | None = None
        self.stopped = False

    def start(self, hwnd: int) -> None:
        if self.fail_start:
            raise RuntimeError("capture failed")
        self.started_hwnd = hwnd
        self.alive = True

    def latest(self) -> Frame | None:
        return self.frame

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        self.stopped = True
        self.alive = False

    def push(self, frame: Frame) -> None:
        self.frame = frame


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)

    def of(self, event_type: type[Event]) -> list[Event]:
        return [e for e in self.events if isinstance(e, event_type)]
