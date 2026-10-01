"""Recent lines with their metrics (tray «Последние реплики», developer History, TZ 4.13)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime

from subvoicer.core.events import (
    Event,
    SpeechDropped,
    SpeechFinished,
    SpeechStarted,
    UtteranceDetected,
)
from subvoicer.core.models import HistoryEntry
from subvoicer.core.ports import EventBus


class History:
    """Keeps the last ``size`` lines; fed by bus events, read from any thread."""

    def __init__(self, size: int = 50) -> None:
        self.size = size
        self._lock = threading.Lock()
        self._entries: OrderedDict[int, HistoryEntry] = OrderedDict()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(UtteranceDetected, self._on_event)
        bus.subscribe(SpeechStarted, self._on_event)
        bus.subscribe(SpeechFinished, self._on_event)
        bus.subscribe(SpeechDropped, self._on_event)

    def recent(self, count: int | None = None) -> list[HistoryEntry]:
        """Newest first."""
        with self._lock:
            entries = list(reversed(self._entries.values()))
        return entries if count is None else entries[:count]

    def get(self, entry_id: int) -> HistoryEntry | None:
        with self._lock:
            return self._entries.get(entry_id)

    def resize(self, size: int) -> None:
        with self._lock:
            self.size = size
            self._trim()

    def _on_event(self, event: Event) -> None:
        with self._lock:
            match event:
                case UtteranceDetected(utterance=u):
                    self._entries[u.id] = HistoryEntry(
                        id=u.id,
                        time=datetime.now(),
                        channel=u.channel,
                        speaker=u.speaker,
                        text=u.text,
                        confidence=u.confidence,
                        ocr_input=u.ocr_input,
                        t_change=u.t_change,
                    )
                    self._trim()
                case SpeechStarted(request=request, rate=rate, t_play=t_play):
                    entry = self._entries.get(request.id)
                    if entry is not None:
                        latency = (
                            (t_play - entry.t_change) * 1000 if entry.t_change is not None else None
                        )
                        self._entries[request.id] = replace(
                            entry,
                            voice=request.voice_id,
                            priority=str(request.priority),
                            rate=rate,
                            latency_ms=latency,
                            outcome="playing",
                        )
                case SpeechFinished(request=request, completed=completed):
                    self._update(request.id, "done" if completed else "cut", request.voice_id)
                case SpeechDropped(request=request, reason=reason):
                    self._update(request.id, f"dropped:{reason}", request.voice_id)

    def _update(self, entry_id: int, outcome: str, voice: str) -> None:
        entry = self._entries.get(entry_id)
        if entry is not None:
            self._entries[entry_id] = replace(entry, outcome=outcome, voice=entry.voice or voice)

    def _trim(self) -> None:
        while len(self._entries) > self.size:
            self._entries.popitem(last=False)
