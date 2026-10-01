"""Speech: scheduler + text preparation + synthesis (with cache) + player, in one thread.

Everything reaches the thread through one queue: new lines, player callbacks, hotkeys.
Long lines are synthesized piece by piece while the first piece already plays (TZ 4.7);
a hurried line re-synthesizes its pieces that have not sounded yet at the new rate.
While a line with ``duck`` is on, the game is ducked along the envelope (TZ 4.10).
"""

from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass, field
from enum import StrEnum

from subvoicer.core.events import SpeechDropped, SpeechFinished, SpeechStarted
from subvoicer.core.models import AudioClip, SpeechRequest
from subvoicer.core.ports import AudioOutput, Clock, Ducker, EventPublisher, TtsEngine
from subvoicer.pipeline.speech.cache import SpeechCache
from subvoicer.pipeline.speech.ducking import DuckEnvelope
from subvoicer.pipeline.speech.scheduler import (
    Command,
    Dropped,
    Hurry,
    OverlapMode,
    Play,
    SpeechScheduler,
    Stop,
)
from subvoicer.pipeline.text.preparer import TextPreparer
from subvoicer.pipeline.text.pronunciation import PronunciationEntry
from subvoicer.services.base import Service

log = logging.getLogger(__name__)


@dataclass
class _Line:
    request: SpeechRequest
    rate: float
    pending: list[str] = field(default_factory=list)  # pieces not synthesized yet
    fed: list[str] = field(default_factory=list)  # pieces given to the player, in order


@dataclass
class GameDucking:
    """Which game to duck and how."""

    ducker: Ducker
    envelope: DuckEnvelope
    pid: int | None  # None: no active game, nothing to duck
    applied: float = 1.0  # the gain last given to the ducker


@dataclass(frozen=True, slots=True)
class _Submit:
    request: SpeechRequest


@dataclass(frozen=True, slots=True)
class _Started:
    line_id: int
    t_play: float


@dataclass(frozen=True, slots=True)
class _Finished:
    line_id: int
    completed: bool


@dataclass(frozen=True, slots=True)
class _Game:
    pid: int | None


@dataclass(frozen=True, slots=True)
class _Pause:
    paused: bool


@dataclass(frozen=True, slots=True)
class SpeechTuning:
    """Settings applied on the fly (TZ 4.14: no restart)."""

    base_rate: float
    overlap: OverlapMode
    hurry_rate: float
    max_lag_s: float
    low_priority_ttl_s: float
    volume: float
    duck_level: float
    duck_fade_s: float
    duck_release_s: float
    cache_size: int
    output_device: str


class _Hotkey(StrEnum):
    SKIP = "skip"
    REPEAT = "repeat"
    TOGGLE_PAUSE = "toggle_pause"


_Message = _Submit | _Started | _Finished | _Hotkey | _Game | _Pause | SpeechTuning


class SpeechService(Service):
    def __init__(
        self,
        scheduler: SpeechScheduler,
        preparer: TextPreparer,
        tts: TtsEngine,
        output: AudioOutput,
        cache: SpeechCache,
        clock: Clock,
        publisher: EventPublisher,
        ducking: GameDucking | None = None,
        interval_s: float = 0.01,
    ) -> None:
        super().__init__("SpeechService", interval_s)
        self._scheduler = scheduler
        self._preparer = preparer
        self._tts = tts
        self._output = output
        self._cache = cache
        self._clock = clock
        self._publisher = publisher
        self._ducking = ducking
        self._inbox: queue.Queue[_Message] = queue.Queue()
        self._lines: dict[int, _Line] = {}
        output.set_listeners(self._on_started, self._on_finished)

    # ------------------------------------------------------------------ any thread

    def submit(self, request: SpeechRequest) -> None:
        self._inbox.put(_Submit(request))

    def skip(self) -> None:
        self._inbox.put(_Hotkey.SKIP)

    def repeat(self) -> None:
        self._inbox.put(_Hotkey.REPEAT)

    def toggle_pause(self) -> None:
        self._inbox.put(_Hotkey.TOGGLE_PAUSE)

    def tune(self, tuning: SpeechTuning) -> None:
        self._inbox.put(tuning)

    def set_paused(self, paused: bool) -> None:
        """Auto voicing off/on (the tray switch); manual lines still play."""
        self._inbox.put(_Pause(paused))

    @property
    def paused(self) -> bool:
        return self._scheduler.paused

    def set_game(self, pid: int | None) -> None:
        """The game to duck from now on; the previous one gets its volume back."""
        self._inbox.put(_Game(pid))

    def _on_started(self, line_id: int, t_play: float) -> None:
        self._inbox.put(_Started(line_id, t_play))

    def _on_finished(self, line_id: int, completed: bool) -> None:
        self._inbox.put(_Finished(line_id, completed))

    # ------------------------------------------------------------------ service thread

    def step(self) -> None:
        now = self._clock.now()
        while True:
            try:
                message = self._inbox.get_nowait()
            except queue.Empty:
                break
            self._handle(message, now)
        self._execute(self._scheduler.tick(now))
        self._duck(now)
        self._synthesize_next_piece()

    def _handle(self, message: _Message, now: float) -> None:
        s = self._scheduler
        match message:
            case _Submit(request=request):
                log.debug(
                    "Речь: реплика %d (%s, %s, голос %s) «%s»",
                    request.id,
                    request.channel,
                    request.priority,
                    request.voice_id,
                    request.text,
                )
                self._execute(s.submit(request, now))
            case _Hotkey.SKIP:
                self._execute(s.skip(now))
            case _Hotkey.REPEAT:
                self._execute(s.repeat(now))
            case _Hotkey.TOGGLE_PAUSE:
                commands = s.set_paused(not s.paused)
                log.info("Автоозвучка %s", "на паузе" if s.paused else "продолжена")
                self._execute(commands)
            case _Pause(paused=paused):
                if paused != s.paused:
                    log.info("Автоозвучка %s", "на паузе" if paused else "продолжена")
                    self._execute(s.set_paused(paused))
            case _Game(pid=pid):
                self._switch_game(pid)
            case SpeechTuning():
                self._tune(message)
            case _Started(line_id=line_id, t_play=t_play):
                line = self._lines.get(line_id)
                if line is not None:
                    log.debug("LATENCY play id=%d t=%.3f", line_id, t_play)
                    self._publisher.publish(SpeechStarted(line.request, line.rate, t_play))
            case _Finished(line_id=line_id, completed=completed):
                finished = self._lines.pop(line_id, None)
                if finished is not None:
                    self._publisher.publish(SpeechFinished(finished.request, completed))
                self._execute(s.finished(line_id, completed, now))

    def _execute(self, commands: list[Command]) -> None:
        for command in commands:
            match command:
                case Play(request=request, rate=rate):
                    self._play(request, rate)
                case Hurry(request=request, rate=rate):
                    self._hurry(request, rate)
                case Stop(request=request):
                    line = self._lines.get(request.id)
                    if line is not None:
                        line.pending.clear()
                    self._output.stop(request.id)
                case Dropped(request=request, reason=reason):
                    log.debug("Речь: реплика %d отброшена (%s)", request.id, reason)
                    self._publisher.publish(SpeechDropped(request, str(reason)))

    def _play(self, request: SpeechRequest, rate: float) -> None:
        dictionary = None
        if request.pronunciation:
            dictionary = self._preparer.dictionary.with_extra(
                PronunciationEntry(source, target, regex)
                for source, target, regex in request.pronunciation
            )
        pieces = self._preparer.prepare(request.text, dictionary)
        if not pieces:
            self._execute(self._scheduler.finished(request.id, True, self._clock.now()))
            return
        started = time.perf_counter()
        clip = self._synthesize(pieces[0], request.voice_id, rate)
        log.debug(
            "Речь: синтез %d мс, скорость %.2f, «%s»",
            (time.perf_counter() - started) * 1000,
            rate,
            pieces[0],
        )
        self._lines[request.id] = _Line(request, rate, pieces[1:], [pieces[0]])
        self._output.start_line(request.id, clip)
        if len(pieces) == 1:
            self._output.finish_feeding(request.id)

    def _synthesize_next_piece(self) -> None:
        current = self._scheduler.current
        line = self._lines.get(current.id) if current is not None else None
        if line is None or not line.pending:
            return
        piece = line.pending.pop(0)
        clip = self._synthesize(piece, line.request.voice_id, line.rate)
        line.fed.append(piece)
        self._output.feed(line.request.id, clip, last=not line.pending)

    def _hurry(self, request: SpeechRequest, rate: float) -> None:
        line = self._lines.get(request.id)
        if line is None:
            return
        dropped = self._output.drop_unplayed(request.id)
        if dropped:
            line.pending = line.fed[-dropped:] + line.pending
            del line.fed[-dropped:]
        log.debug(
            "Речь: реплика %d ускорена до %.2f, пересинтез %d частей",
            request.id,
            rate,
            len(line.pending),
        )
        line.rate = rate

    def _tune(self, t: SpeechTuning) -> None:
        s = self._scheduler
        s.base_rate, s.overlap, s.hurry_rate = t.base_rate, t.overlap, t.hurry_rate
        s.max_lag_s, s.low_priority_ttl_s = t.max_lag_s, t.low_priority_ttl_s
        self._cache.resize(t.cache_size)
        self._output.set_volume(t.volume)
        if self._ducking is not None:
            envelope = self._ducking.envelope
            envelope.level, envelope.fade_s, envelope.release_s = (
                t.duck_level,
                t.duck_fade_s,
                t.duck_release_s,
            )
        try:
            self._output.set_device(t.output_device or None)
        except Exception:
            log.exception("Не удалось открыть устройство вывода %r", t.output_device)
        log.info("Настройки озвучки применены (скорость %.2f, %s)", t.base_rate, t.overlap)

    def _switch_game(self, pid: int | None) -> None:
        d = self._ducking
        if d is None or pid == d.pid:
            return
        if d.pid is not None and d.applied != 1.0:
            try:
                d.ducker.set_gain(d.pid, 1.0)
            except Exception:
                log.exception("Ducking: не удалось вернуть громкость прежней игры")
        d.pid, d.applied = pid, 1.0

    def _duck(self, now: float) -> None:
        """Ducking starts with the Play command: the fade overlaps the first synthesis."""
        d = self._ducking
        if d is None or d.pid is None:
            return
        current = self._scheduler.current
        gain = round(d.envelope.update(current is not None and current.duck, now), 3)
        if gain == d.applied:
            return
        d.applied = gain
        try:
            d.ducker.set_gain(d.pid, gain)
        except Exception:
            log.exception("Ducking: ошибка смены громкости")

    def _synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip:
        clip = self._cache.get(text, voice_id, rate)
        if clip is None:
            clip = self._tts.synthesize(text, voice_id, rate)
            self._cache.put(text, voice_id, rate, clip)
        return clip
