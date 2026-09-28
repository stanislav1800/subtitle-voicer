"""Composition root: engines from registries -> sources -> services."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from subvoicer.app.runtime import MonotonicClock, Session
from subvoicer.config.resources import short_words, speaker_stoplist
from subvoicer.core.models import Rect
from subvoicer.core.ports import EventPublisher
from subvoicer.engines import registry
from subvoicer.pipeline.dedup import Deduplicator
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec
from subvoicer.services.capture_service import CaptureService
from subvoicer.services.zone_service import ZoneService
from subvoicer.sources.zone_ocr import ZoneOcrSource, ZoneSpec

log = logging.getLogger(__name__)


Hsv = tuple[int, int, int]

MASK_PRESETS = tuple(p.value for p in MaskPreset)
SPEAKER_MODES = (SpeakerMode.PREFIX.value, SpeakerMode.NONE.value)


@dataclass(frozen=True)
class WatchConfig:
    """One ``auto`` zone watched on one window (CLI ``watch``, TZ stage 2).

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


def load_ocr(engine_id: str, options: dict[str, Any]) -> Any:
    engine = registry.resolve("ocr", engine_id)(**options)
    engine.load()
    return engine


def build_watch(config: WatchConfig, publisher: EventPublisher) -> Session:
    from subvoicer.engines.system import windows

    clock = MonotonicClock()
    factories: dict[str, Any] = {}
    for mode in config.capture_modes:
        cls = registry.resolve("capture", mode)
        if cls.is_available():
            factories[mode] = cls
    if "wgc" not in factories:
        raise RuntimeError("Захват окна (Windows Graphics Capture) недоступен")

    capture = CaptureService(
        config.hwnd,
        factories,
        clock,
        windows.is_window,
        publisher,
    )
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
    zones = ZoneService(capture.latest, source, publisher, config.poll_hz)
    return Session([capture, zones])
