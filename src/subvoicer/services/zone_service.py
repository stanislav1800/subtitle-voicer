"""Runs the zone source at poll_hz on the latest captured frame and publishes utterances."""

from __future__ import annotations

from collections.abc import Callable

from subvoicer.core.events import UtteranceDetected
from subvoicer.core.models import Frame
from subvoicer.core.ports import EventPublisher
from subvoicer.services.base import Service
from subvoicer.sources.zone_ocr import ZoneOcrSource


class ZoneService(Service):
    def __init__(
        self,
        latest_frame: Callable[[], Frame | None],
        source: ZoneOcrSource,
        publisher: EventPublisher,
        poll_hz: float = 10,
    ) -> None:
        super().__init__("ZoneService", 1.0 / poll_hz)
        self._latest_frame = latest_frame
        self._source = source
        self._publisher = publisher

    def step(self) -> None:
        frame = self._latest_frame()
        if frame is None:
            return
        for utterance in self._source.process(frame):
            self._publisher.publish(UtteranceDetected(utterance))
