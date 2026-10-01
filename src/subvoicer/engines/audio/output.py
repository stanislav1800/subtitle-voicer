"""Audio output through sounddevice: one always-open stream, one line at a time, pieces of
a line are appended while it plays, a cut line fades out (~100 ms)."""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from subvoicer.core.models import AudioClip

log = logging.getLogger(__name__)

Samples = npt.NDArray[np.float32]
StartedListener = Callable[[int, float], None]  # (line id, monotonic time of the first sound)
FinishedListener = Callable[[int, bool], None]  # (line id, played to the end)


@dataclass
class _Line:
    id: int
    chunks: deque[Samples] = field(default_factory=deque)
    offset: int = 0  # position inside chunks[0]
    fed_all: bool = False
    started: bool = False
    fade_left: int = 0  # samples of fade-out left; 0 = not fading
    fade_total: int = 0


class SoundDeviceOutput:
    id = "sounddevice"

    def __init__(
        self,
        device: str | int | None = None,
        sample_rate: int = 48_000,
        volume: float = 1.0,
        fade_ms: int = 100,
        block: int = 960,  # 20 ms at 48 kHz
    ) -> None:
        self.device = device or None
        self.sample_rate = sample_rate
        self.volume = volume
        self.fade_ms = fade_ms
        self._block = block
        self._lock = threading.Lock()
        self._line: _Line | None = None
        self._stream: Any = None
        self._on_started: StartedListener = lambda _id, _t: None
        self._on_finished: FinishedListener = lambda _id, _done: None

    @classmethod
    def is_available(cls) -> bool:
        return importlib.util.find_spec("sounddevice") is not None

    def set_listeners(self, on_started: StartedListener, on_finished: FinishedListener) -> None:
        self._on_started, self._on_finished = on_started, on_finished

    def load(self) -> None:
        import sounddevice as sd

        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self._block,
            device=self.device,
            latency="low",
            callback=self._callback,
        )
        self._stream.start()
        log.info("Звук: %s, %d Гц", self._stream.device, self.sample_rate)

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))

    def set_device(self, device: str | None) -> None:
        if (device or None) == self.device:
            return
        self.close()
        with self._lock:
            replaced, self._line = self._line, None
        if replaced is not None:
            self._on_finished(replaced.id, False)
        self.device = device or None
        self.load()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    # ------------------------------------------------------------------ control

    def start_line(self, line_id: int, clip: AudioClip) -> None:
        """Start a new line; a line still playing is cut without fade (callers fade first)."""
        replaced = None
        with self._lock:
            if self._line is not None:
                replaced = self._line.id
            self._line = _Line(line_id, deque([self._convert(clip)]))
        if replaced is not None:
            self._on_finished(replaced, False)

    def feed(self, line_id: int, clip: AudioClip, last: bool = False) -> None:
        with self._lock:
            if self._line is not None and self._line.id == line_id:
                self._line.chunks.append(self._convert(clip))
                self._line.fed_all = self._line.fed_all or last

    def finish_feeding(self, line_id: int) -> None:
        with self._lock:
            if self._line is not None and self._line.id == line_id:
                self._line.fed_all = True

    def drop_unplayed(self, line_id: int) -> int:
        """Remove the pieces of ``line_id`` that have not started to sound; the piece that
        is sounding now plays to its end. Returns how many pieces were removed."""
        with self._lock:
            line = self._line
            if line is None or line.id != line_id or line.fade_total:
                return 0
            keep = 1 if line.chunks else 0
            dropped = len(line.chunks) - keep
            while len(line.chunks) > keep:
                line.chunks.pop()
            if dropped:
                line.fed_all = False
            return dropped

    def stop(self, line_id: int, fade_ms: int | None = None) -> None:
        with self._lock:
            line = self._line
            if line is None or line.id != line_id or line.fade_left:
                return
            fade = round((self.fade_ms if fade_ms is None else fade_ms) * self.sample_rate / 1000)
            line.fade_left = line.fade_total = max(fade, 1)

    def is_playing(self) -> bool:
        with self._lock:
            return self._line is not None

    # ------------------------------------------------------------------ audio thread

    def _convert(self, clip: AudioClip) -> Samples:
        samples = np.asarray(clip.samples, dtype=np.float32)
        if clip.sample_rate != self.sample_rate and len(samples):
            positions = np.linspace(
                0, len(samples) - 1, round(len(samples) * self.sample_rate / clip.sample_rate)
            )
            samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        return samples

    def _callback(self, outdata: Any, frames: int, _time: Any, _status: Any) -> None:
        out = outdata[:, 0]
        out.fill(0.0)
        events: list[tuple[str, int, bool]] = []
        with self._lock:
            line = self._line
            if line is not None:
                written = self._fill(line, out, frames)
                if written and not line.started:
                    line.started = True
                    events.append(("started", line.id, True))
                if line.fade_total:
                    self._apply_fade(line, out, written)
                    if line.fade_left == 0:
                        self._line = None
                        events.append(("finished", line.id, False))
                elif not line.chunks and line.fed_all:
                    self._line = None
                    events.append(("finished", line.id, True))
        out *= self.volume
        now = time.monotonic()
        for kind, line_id, done in events:
            if kind == "started":
                self._on_started(line_id, now)
            else:
                self._on_finished(line_id, done)

    @staticmethod
    def _fill(line: _Line, out: Samples, frames: int) -> int:
        pos = 0
        while pos < frames and line.chunks:
            chunk = line.chunks[0]
            take = min(frames - pos, len(chunk) - line.offset)
            out[pos : pos + take] = chunk[line.offset : line.offset + take]
            pos += take
            line.offset += take
            if line.offset >= len(chunk):
                line.chunks.popleft()
                line.offset = 0
        return pos

    @staticmethod
    def _apply_fade(line: _Line, out: Samples, written: int) -> None:
        n = min(written, line.fade_left)
        start = line.fade_left / line.fade_total
        end = (line.fade_left - n) / line.fade_total
        out[:n] *= np.linspace(start, end, n, endpoint=False, dtype=np.float32)
        out[n:] = 0.0
        line.fade_left = 0 if written == 0 else line.fade_left - n


def output_devices() -> list[str]:
    """Names of the output devices of the default host API (what ``device=`` accepts)."""
    import sounddevice as sd

    hostapi = sd.default.hostapi
    names = []
    for device in sd.query_devices():
        if device["max_output_channels"] > 0 and device["hostapi"] == hostapi:
            names.append(str(device["name"]))
    return names
