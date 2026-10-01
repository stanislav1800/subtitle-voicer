"""Fake TTS engine and audio output for speech service tests."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from subvoicer.core.models import AudioClip, Gender, Voice


class FakeTts:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def voices(self) -> list[Voice]:
        return [Voice("v1", Gender.MALE, "V1"), Voice("v2", Gender.FEMALE, "V2")]

    def synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip:
        self.calls.append((text, voice_id, rate))
        return AudioClip(np.zeros(480, dtype=np.float32), 48_000)


class FakeOutput:
    """Records what the service asks; the test plays the role of the audio thread."""

    def __init__(self) -> None:
        self.log: list[tuple[str, int]] = []
        self.fed: dict[int, int] = {}  # line id -> pieces received
        self.done_feeding: set[int] = set()
        self._started: Callable[[int, float], None] = lambda _i, _t: None
        self._finished: Callable[[int, bool], None] = lambda _i, _d: None

    def set_listeners(
        self, on_started: Callable[[int, float], None], on_finished: Callable[[int, bool], None]
    ) -> None:
        self._started, self._finished = on_started, on_finished

    def start_line(self, line_id: int, clip: AudioClip) -> None:
        self.log.append(("start", line_id))
        self.fed[line_id] = 1

    def feed(self, line_id: int, clip: AudioClip, last: bool = False) -> None:
        self.fed[line_id] = self.fed.get(line_id, 0) + 1
        if last:
            self.done_feeding.add(line_id)

    def finish_feeding(self, line_id: int) -> None:
        self.done_feeding.add(line_id)

    def drop_unplayed(self, line_id: int) -> int:
        """Pretends the first piece is sounding and the rest has not started."""
        pieces = self.fed.get(line_id, 0)
        dropped = max(0, pieces - 1)
        self.fed[line_id] = pieces - dropped
        self.done_feeding.discard(line_id)
        self.log.append(("drop", line_id))
        return dropped

    def stop(self, line_id: int, fade_ms: int | None = None) -> None:
        self.log.append(("stop", line_id))

    # the audio thread's side
    def sound_starts(self, line_id: int, t: float) -> None:
        self._started(line_id, t)

    def line_ends(self, line_id: int, completed: bool = True) -> None:
        self._finished(line_id, completed)


class FakeDucker:
    def __init__(self) -> None:
        self.gains: list[tuple[int, float]] = []
        self.restored = 0

    def set_gain(self, pid: int, gain: float) -> None:
        self.gains.append((pid, gain))

    def restore_all(self) -> None:
        self.restored += 1
