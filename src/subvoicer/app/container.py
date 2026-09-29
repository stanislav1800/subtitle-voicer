"""Composition root: engines from registries -> sources -> services."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from subvoicer.app.runtime import MonotonicClock, Session
from subvoicer.config.resources import (
    gender_exceptions,
    letter_names,
    short_words,
    speaker_stoplist,
    translit_table,
    voice_catalog,
)
from subvoicer.core.errors import EngineError
from subvoicer.core.events import EngineUnavailable, UtteranceDetected
from subvoicer.core.models import Priority, Rect, SpeechRequest, Utterance, UtteranceKind
from subvoicer.core.ports import EventBus, EventPublisher
from subvoicer.engines import registry
from subvoicer.pipeline.dedup import Deduplicator
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.gender import GenderGuesser
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.speech.cache import SpeechCache
from subvoicer.pipeline.speech.scheduler import OverlapMode, SpeechScheduler
from subvoicer.pipeline.speech.voices import VoiceMode, VoicePicker, default_voices
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.preparer import TextPreparer
from subvoicer.pipeline.text.pronunciation import PronunciationDict, PronunciationEntry
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec
from subvoicer.services.capture_service import CaptureService
from subvoicer.services.speech_service import SpeechService
from subvoicer.services.zone_service import ZoneService
from subvoicer.sources.zone_ocr import ZoneOcrSource, ZoneSpec

log = logging.getLogger(__name__)

Hsv = tuple[int, int, int]

MASK_PRESETS = tuple(p.value for p in MaskPreset)
SPEAKER_MODES = (SpeakerMode.PREFIX.value, SpeakerMode.NONE.value)
VOICE_MODES = tuple(m.value for m in VoiceMode)
OVERLAP_MODES = tuple(m.value for m in OverlapMode)
PRIORITIES = (Priority.HIGH.value, Priority.NORMAL.value, Priority.LOW.value)
TTS_CHAIN = ("silero", "sapi5")  # fallback order (TZ 4.8)

DEFAULT_HOTKEYS = {  # TZ 4.12
    "pause": "<ctrl>+<alt>+p",
    "repeat": "<ctrl>+<alt>+r",
    "skip": "<ctrl>+<alt>+s",
}


@dataclass(frozen=True)
class SpeechConfig:
    """Voicing of the watched zone (plain values: the CLI must not import pipeline types)."""

    dictionary_path: Path | None = None
    voice_mode: str = "speaker"  # one of VOICE_MODES
    voice_id: str | None = None  # for voice_mode "fixed"
    priority: str = "high"
    base_rate: float = 1.0
    overlap: str = "speedup"  # one of OVERLAP_MODES (TZ 4.9)
    hurry_rate: float = 1.5
    max_lag_s: float = 8.0
    volume: float = 1.0
    output_device: str = ""
    tts: tuple[str, ...] = TTS_CHAIN
    hotkeys: bool = True
    low_priority_ttl_s: float = 5.0
    cache_size: int = 200


@dataclass(frozen=True)
class WatchConfig:
    """One ``auto`` zone watched on one window (CLI ``watch``, TZ stages 2-3).

    Plain values only: the CLI must not import pipeline types.
    """

    hwnd: int
    rect: Rect
    preset: str  # one of MASK_PRESETS
    colors_hsv: tuple[tuple[Hsv, Hsv], ...] = ()
    threshold: int | None = None
    speaker: str = "none"  # one of SPEAKER_MODES
    poll_hz: float = 10
    ocr_engine: str = "rapidocr"
    ocr_options: dict[str, Any] = field(default_factory=lambda: {"model": "cyrillic"})
    capture_modes: tuple[str, ...] = ("wgc", "dxgi")
    speech: SpeechConfig | None = None  # None: print only (stage 2)


def load_ocr(engine_id: str, options: dict[str, Any]) -> Any:
    engine = registry.resolve("ocr", engine_id)(**options)
    engine.load()
    return engine


def load_tts(chain: tuple[str, ...], publisher: EventPublisher) -> Any:
    """The first engine of the chain that is available and loads (TZ 5 / ARCHITECTURE 5)."""
    for engine_id in chain:
        cls = registry.resolve("tts", engine_id)
        if not cls.is_available():
            continue
        options = {"catalog": voice_catalog("silero_v5")} if engine_id == "silero" else {}
        engine = cls(**options)
        try:
            engine.load()
        except EngineError as exc:
            log.warning("TTS %s не загрузился: %s", engine_id, exc)
            continue
        return engine
    publisher.publish(EngineUnavailable("tts", ",".join(chain), "ни один движок не загрузился"))
    raise EngineError("Нет доступного движка синтеза речи")


def load_dictionary(path: Path | None) -> PronunciationDict:
    """``--dict``: a JSON list of ``{"from": ..., "to": ..., "regex": false}`` (TZ 4.7.1)."""
    if path is None:
        return PronunciationDict()
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = [
        PronunciationEntry(str(item["from"]), str(item["to"]), bool(item.get("regex", False)))
        for item in data
    ]
    log.info("Словарь произношений: %d записей из %s", len(entries), path)
    return PronunciationDict(entries)


def build_speech(config: SpeechConfig, bus: EventBus) -> tuple[Session, SpeechService]:
    clock = MonotonicClock()
    from subvoicer.engines.audio.output import SoundDeviceOutput
    from subvoicer.engines.tts.silero_stress import SileroStressMarker

    tts = load_tts(config.tts, bus)
    stress = SileroStressMarker()
    stress.load()
    preparer = TextPreparer(
        stress,
        LatinReader(letter_names(), translit_table()),
        load_dictionary(config.dictionary_path),
    )
    output = SoundDeviceOutput(device=config.output_device or None, volume=config.volume)
    output.load()
    service = SpeechService(
        SpeechScheduler(
            config.base_rate,
            config.low_priority_ttl_s,
            overlap=OverlapMode(config.overlap),
            hurry_rate=config.hurry_rate,
            max_lag_s=config.max_lag_s,
        ),
        preparer,
        tts,
        output,
        SpeechCache(config.cache_size),
        clock,
        bus,
    )
    picker = VoicePicker(default_voices(tts.voices()), GenderGuesser(gender_exceptions()))
    log.info(
        "Голоса: рассказчик %s, мужской %s, женский %s",
        picker.defaults.narrator,
        picker.defaults.male,
        picker.defaults.female,
    )
    router = LineRouter(
        picker, VoiceMode(config.voice_mode), config.voice_id, Priority(config.priority)
    )
    bus.subscribe(UtteranceDetected, lambda event: service.submit(router.request(event.utterance)))
    closers: list[Callable[[], None]] = [output.close]
    starters: list[Callable[[], None]] = []
    if config.hotkeys:
        from subvoicer.engines.system.hotkeys import PynputHotkeys

        hotkeys = PynputHotkeys()
        actions = {"pause": service.toggle_pause, "repeat": service.repeat, "skip": service.skip}
        bindings = {DEFAULT_HOTKEYS[name]: action for name, action in actions.items()}
        starters.append(lambda: hotkeys.start(bindings))
        closers.insert(0, hotkeys.stop)
    return Session([service], closers, starters), service


class LineRouter:
    """Utterance -> SpeechRequest for the watched zone: voice, priority, continuation.
    Profiles and per-zone VoicingPolicy replace the fixed settings on stage 5."""

    def __init__(
        self, picker: VoicePicker, mode: VoiceMode, fixed_id: str | None, priority: Priority
    ) -> None:
        self._picker = picker
        self._mode = mode
        self._fixed_id = fixed_id
        self._priority = priority
        self._last_voice: dict[str, str] = {}

    def request(self, utterance: Utterance) -> SpeechRequest:
        continuation = utterance.kind is UtteranceKind.CONTINUATION
        if continuation and utterance.channel in self._last_voice:
            voice = self._last_voice[utterance.channel]  # a continuation keeps the voice (4.4)
        else:
            voice = self._picker.pick(self._mode, utterance.text, self._fixed_id)
        self._last_voice[utterance.channel] = voice
        return SpeechRequest(
            id=utterance.id,
            text=utterance.text,
            voice_id=voice,
            priority=self._priority,
            continuation=continuation,
            channel=utterance.channel,
            speaker=utterance.speaker,
            t_change=utterance.t_change,
        )


def build_watch(config: WatchConfig, bus: EventBus) -> Session:
    from subvoicer.engines.system import windows

    clock = MonotonicClock()
    factories: dict[str, Any] = {}
    for mode in config.capture_modes:
        cls = registry.resolve("capture", mode)
        if cls.is_available():
            factories[mode] = cls
    if "wgc" not in factories:
        raise RuntimeError("Захват окна (Windows Graphics Capture) недоступен")

    capture = CaptureService(config.hwnd, factories, clock, windows.is_window, bus)
    zone = ZoneSpec(
        id="zone",
        rect=config.rect,
        mask=MaskSpec(
            MaskPreset(config.preset), colors_hsv=config.colors_hsv, threshold=config.threshold
        ),
        speaker=SpeakerResolver(SpeakerMode(config.speaker), speaker_stoplist()),
        ocr_input=OcrInput.RAW,
    )
    source = ZoneOcrSource(
        [zone],
        OcrRunner(load_ocr(config.ocr_engine, config.ocr_options)),
        JunkFilter(short_words()),
        Deduplicator(),
        clock,
    )
    zones = ZoneService(capture.latest, source, bus, config.poll_hz)
    watch = Session([capture, zones])
    if config.speech is None:
        return watch
    speech, _ = build_speech(config.speech, bus)
    return Session([speech, watch])  # speech first: it must be ready for the first line
