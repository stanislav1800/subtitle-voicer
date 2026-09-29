"""SpeechScheduler: the voicing policy of TZ 4.9 as a pure state machine.

Every call returns commands for the speech service (play / hurry / stop with fade /
dropped); the player reports back with ``finished``. Time comes in as an argument.

What happens when a line of the same priority arrives while one is playing is the
``overlap`` mode (TZ 1.14): cut the old one, wait for it, hurry it, or adapt the speed to
the queue. Higher priority always cuts; continuations always follow their line.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from enum import StrEnum

from subvoicer.core.models import Priority, SpeechRequest

MAX_SPEEDUP = 1.3
SPEEDUP_STEP = 0.15
REPEAT_ID_START = 10**9  # ids of repeats never clash with utterance ids

_RANK = {Priority.LOW: 0, Priority.NORMAL: 1, Priority.HIGH: 2, Priority.MANUAL: 3}


@dataclass(frozen=True, slots=True)
class Play:
    request: SpeechRequest
    rate: float


@dataclass(frozen=True, slots=True)
class Hurry:
    """Play the not-yet-sounding rest of the current line at ``rate``."""

    request: SpeechRequest
    rate: float


@dataclass(frozen=True, slots=True)
class Stop:
    """Cut the current line with a short fade (~100 ms)."""

    request: SpeechRequest


class DropReason(StrEnum):
    PAUSED = "paused"
    MANUAL = "manual"  # a manual line is playing or took over
    EXPIRED = "expired"  # waited longer than low_priority_ttl_s / max_lag_s
    INTERRUPTED = "interrupted"  # continuation of a line that was cut


@dataclass(frozen=True, slots=True)
class Dropped:
    request: SpeechRequest
    reason: DropReason


Command = Play | Hurry | Stop | Dropped


class OverlapMode(StrEnum):
    CUT = "cut"  # the new line cuts the old one (with auto speed-up after repeated cuts)
    QUEUE = "queue"  # the new line waits for the old one
    SPEEDUP = "speedup"  # the old line hurries (x hurry_rate), the waiting one catches up
    ADAPTIVE = "adaptive"  # nothing is cut; the longer the queue, the faster everything


@dataclass(slots=True)
class _Queued:
    request: SpeechRequest
    queued_at: float
    deadline: float | None  # None: waits as long as needed (continuations)


def speedup(interrupts: int) -> float:
    """×1.15 after two cut ``high`` lines in a row, ×1.3 after three and more."""
    if interrupts < 2:
        return 1.0
    return min(MAX_SPEEDUP, 1.0 + SPEEDUP_STEP * (interrupts - 1))


class SpeechScheduler:
    def __init__(
        self,
        base_rate: float = 1.0,
        low_priority_ttl_s: float = 5.0,
        overlap: OverlapMode = OverlapMode.SPEEDUP,
        hurry_rate: float = 1.5,
        catchup_rate: float = 1.15,
        max_lag_s: float = 8.0,
        adaptive_step: float = 0.2,
        adaptive_max: float = 1.6,
    ) -> None:
        self.base_rate = base_rate
        self.low_priority_ttl_s = low_priority_ttl_s
        self.overlap = overlap
        self.hurry_rate = hurry_rate
        self.catchup_rate = catchup_rate
        self.max_lag_s = max_lag_s
        self.adaptive_step = adaptive_step
        self.adaptive_max = adaptive_max
        self.paused = False
        self.interrupts = 0
        self.current: SpeechRequest | None = None
        self.current_rate = base_rate
        self.last_played: SpeechRequest | None = None
        self._queue: list[_Queued] = []
        self._rate_by_channel: dict[str, float] = {}
        self._cut_channels: set[str] = set()  # the last line of these channels was cut
        self._repeat_ids = itertools.count(REPEAT_ID_START)

    @property
    def queued(self) -> list[SpeechRequest]:
        return [q.request for q in self._queue]

    # ------------------------------------------------------------------ events

    def submit(self, request: SpeechRequest, now: float) -> list[Command]:
        if request.priority is Priority.MANUAL:
            return self._take_over(request, now)
        if self.paused:
            return [Dropped(request, DropReason.PAUSED)]
        current = self.current
        if current is not None and current.priority is Priority.MANUAL:
            return [Dropped(request, DropReason.MANUAL)]
        if request.continuation and request.channel in self._cut_channels:
            return [Dropped(request, DropReason.INTERRUPTED)]
        if current is None:
            return self._play(request)
        if request.continuation:
            self._queue_continuation(request, now)
            return []
        same = _RANK[request.priority] == _RANK[current.priority]
        if _RANK[request.priority] > _RANK[current.priority] or (
            same and self.overlap is OverlapMode.CUT
        ):
            if current.priority is Priority.HIGH and request.priority is Priority.HIGH:
                self.interrupts += 1
            return [*self._cut_current(), *self._play(request)]
        if same:  # queue / speedup / adaptive: wait behind the current line
            self._queue_by_priority(request, now, ttl=self.max_lag_s)
            return self._hurry_current()
        self._queue_by_priority(request, now)
        return []

    def finished(self, request_id: int, completed: bool, now: float) -> list[Command]:
        """The player finished ``request_id`` (``completed``: played to the end)."""
        current = self.current
        if current is None or current.id != request_id:
            return []  # a line we already cut: its end was reported late
        if completed and current.priority is Priority.HIGH:
            self.interrupts = 0
        self.current = None
        return self._next(now)

    def skip(self, now: float) -> list[Command]:
        if self.current is None:
            return []
        return [*self._cut_current(), *self._next(now)]

    def repeat(self, now: float) -> list[Command]:
        """Play the last voiced line again as a manual line (the audio comes from the cache)."""
        if self.last_played is None:
            return []
        again = replace(
            self.last_played,
            id=next(self._repeat_ids),
            priority=Priority.MANUAL,
            continuation=False,
        )
        return self.submit(again, now)

    def set_paused(self, paused: bool) -> list[Command]:
        """Pause auto voicing: the current auto line stops, the queue is dropped."""
        self.paused = paused
        if not paused:
            return []
        commands: list[Command] = [Dropped(q.request, DropReason.PAUSED) for q in self._queue]
        self._queue.clear()
        if self.current is not None and self.current.priority is not Priority.MANUAL:
            commands.append(self._stop_current())
        return commands

    def tick(self, now: float) -> list[Command]:
        expired = [q for q in self._queue if q.deadline is not None and now > q.deadline]
        for q in expired:
            self._queue.remove(q)
        return [Dropped(q.request, DropReason.EXPIRED) for q in expired]

    # ------------------------------------------------------------------ internals

    def _take_over(self, request: SpeechRequest, now: float) -> list[Command]:
        commands: list[Command] = [Dropped(q.request, DropReason.MANUAL) for q in self._queue]
        self._queue.clear()
        commands += self._cut_current()
        commands += self._play(request)
        return commands

    def _waiting(self) -> int:
        return sum(1 for q in self._queue if not q.request.continuation)

    def _adaptive(self) -> float:
        return min(self.adaptive_max, 1.0 + self.adaptive_step * self._waiting())

    def _rate_for(self, request: SpeechRequest, waited: bool) -> float:
        if request.continuation and request.channel in self._rate_by_channel:
            return self._rate_by_channel[request.channel]
        match self.overlap:
            case OverlapMode.CUT:
                boost = speedup(self.interrupts) if request.priority is Priority.HIGH else 1.0
            case OverlapMode.QUEUE:
                boost = 1.0
            case OverlapMode.SPEEDUP:
                boost = 1.0
                if self._waiting():  # others wait behind this line: hurry
                    boost = self.hurry_rate
                elif waited:  # this line waited: catch up with the screen
                    boost = self.catchup_rate
            case OverlapMode.ADAPTIVE:
                boost = self._adaptive()
        return self.base_rate * boost

    def _hurry_current(self) -> list[Command]:
        if self.current is None or self.current.priority is Priority.MANUAL:
            return []
        match self.overlap:
            case OverlapMode.SPEEDUP:
                rate = self.base_rate * self.hurry_rate
            case OverlapMode.ADAPTIVE:
                rate = self.base_rate * self._adaptive()
            case _:
                return []
        if rate <= self.current_rate + 1e-9:
            return []
        self.current_rate = rate
        self._rate_by_channel[self.current.channel] = rate  # its continuation hurries too
        return [Hurry(self.current, rate)]

    def _play(self, request: SpeechRequest, waited: bool = False) -> list[Command]:
        rate = self._rate_for(request, waited)
        self.current = request
        self.current_rate = rate
        self._rate_by_channel[request.channel] = rate
        self._cut_channels.discard(request.channel)
        if request.priority is not Priority.MANUAL:
            self.last_played = request
        return [Play(request, rate)]

    def _cut_current(self) -> list[Command]:
        if self.current is None:
            return []
        cut = self.current
        self._cut_channels.add(cut.channel)
        commands: list[Command] = [self._stop_current()]
        # the queued tail of the cut line makes no sense on its own
        tails = [
            q for q in self._queue if q.request.continuation and q.request.channel == cut.channel
        ]
        for q in tails:
            self._queue.remove(q)
            commands.append(Dropped(q.request, DropReason.INTERRUPTED))
        return commands

    def _stop_current(self) -> Stop:
        assert self.current is not None
        stop = Stop(self.current)
        self.current = None
        return stop

    def _next(self, now: float) -> list[Command]:
        commands: list[Command] = list(self.tick(now))
        if self._queue:
            commands += self._play(self._queue.pop(0).request, waited=True)
        return commands

    def _queue_continuation(self, request: SpeechRequest, now: float) -> None:
        position = 0
        while position < len(self._queue) and self._queue[position].request.continuation:
            position += 1
        self._queue.insert(position, _Queued(request, now, None))

    def _queue_by_priority(
        self, request: SpeechRequest, now: float, ttl: float | None = None
    ) -> None:
        entry = _Queued(request, now, now + (self.low_priority_ttl_s if ttl is None else ttl))
        position = len(self._queue)
        for i, q in enumerate(self._queue):
            if q.request.continuation:
                continue
            if _RANK[request.priority] > _RANK[q.request.priority]:
                position = i
                break
        self._queue.insert(position, entry)
