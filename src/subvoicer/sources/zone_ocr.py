"""Zones with ``trigger = auto``: frame -> mask -> stabiliser -> OCR -> junk filter -> dedup ->
speaker -> Utterance. No threads here: ZoneService calls ``process`` at poll_hz.

A speaker name may come from a separate speaker zone or from a colour inside the zone
(TZ 4.5): it is read only for a new line, together with the text zone.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from subvoicer.core.models import Frame, Rect, Utterance, UtteranceKind
from subvoicer.core.ports import Clock
from subvoicer.pipeline.dedup import DedupKind, Deduplicator
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.resolver import SpeakerResolver
from subvoicer.pipeline.vision.change import mask_empty
from subvoicer.pipeline.vision.crop import crop
from subvoicer.pipeline.vision.masks import Image, MaskSpec, build_mask
from subvoicer.pipeline.vision.regions import cut_out, paint_out, without
from subvoicer.pipeline.vision.stability import (
    Emptied,
    RunOcr,
    StabilizationSettings,
    ZoneStabilizer,
)

log = logging.getLogger(__name__)


def drop_last_word(text: str) -> str:
    """``"Охрана на входе сме"`` -> ``"Охрана на входе"``; a single word is kept."""
    head, _, _ = text.rstrip().rpartition(" ")
    return head.rstrip() if head.strip() else text.strip()


@dataclass(frozen=True)
class NameSource:
    """Where the speaker candidate is read: a speaker zone (``rect`` + its mask) or, with
    ``rect`` None, the pixels of the name colour inside the text zone."""

    mask: MaskSpec
    rect: Rect | None = None


@dataclass(frozen=True)
class ZoneSpec:
    id: str
    rect: Rect
    mask: MaskSpec
    speaker: SpeakerResolver
    ocr_input: OcrInput = OcrInput.RAW
    stabilization: StabilizationSettings = field(default_factory=StabilizationSettings)
    repeat_cooldown_min: float = 0
    name: NameSource | None = None
    runner: OcrRunner | None = None  # per-zone OCR engine or settings; else the source's
    junk: JunkFilter | None = None  # per-zone min_confidence; else the source's


@dataclass
class _ZoneState:
    spec: ZoneSpec
    stabilizer: ZoneStabilizer
    frame_ts: float | None = None
    frame: Image | None = None
    crop: Image | None = None
    mask: Image | None = None
    name_mask: Image | None = None  # name colour pixels (speaker mode color)
    speaker: str | None = None  # speaker of the last new line (a continuation keeps it)


class ZoneOcrSource:
    def __init__(
        self,
        zones: Sequence[ZoneSpec],
        runner: OcrRunner,
        junk: JunkFilter,
        dedup: Deduplicator,
        clock: Clock,
    ) -> None:
        self._zones = [_ZoneState(z, ZoneStabilizer(z.stabilization)) for z in zones]
        self._runner = runner
        self._junk = junk
        self._dedup = dedup
        self._clock = clock
        self._ids = itertools.count(1)

    def process(self, frame: Frame) -> list[Utterance]:
        """Feed the latest frame (the same frame again is fine: time still moves on, which
        lets a zone settle when the window stops sending frames)."""
        now = self._clock.now()
        found = []
        for zone in self._zones:
            utterance = self._process_zone(zone, frame, now)
            if utterance is not None:
                found.append(utterance)
        return found

    def _process_zone(self, zone: _ZoneState, frame: Frame, now: float) -> Utterance | None:
        spec, stabilizer = zone.spec, zone.stabilizer
        if frame.timestamp != zone.frame_ts or zone.mask is None or zone.crop is None:
            zone.frame_ts = frame.timestamp
            zone.frame = frame.image
            zone.crop = crop(frame.image, spec.rect)
            zone.mask = build_mask(zone.crop, spec.mask)
            if spec.name is not None and spec.name.rect is None:
                zone.name_mask = build_mask(zone.crop, spec.name.mask)
                zone.mask = without(zone.mask, zone.name_mask)
            event_time = frame.timestamp
        else:
            event_time = now  # no new frame: only time passes

        before = stabilizer.state
        decision = stabilizer.update(zone.mask, event_time)
        if stabilizer.state is not before:
            log.debug("Зона %s: %s -> %s", spec.id, before, stabilizer.state)
        if isinstance(decision, Emptied) or decision is None:
            return None
        assert isinstance(decision, RunOcr)
        return self._recognize(zone, decision)

    def _recognize(self, zone: _ZoneState, decision: RunOcr) -> Utterance | None:
        spec, stabilizer = zone.spec, zone.stabilizer
        assert zone.crop is not None and zone.mask is not None
        runner = spec.runner or self._runner
        image = zone.crop if zone.name_mask is None else paint_out(zone.crop, zone.name_mask)
        started = time.perf_counter()
        outcome = runner.run(image, zone.mask, spec.ocr_input)
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.debug(
            "Зона %s: OCR %.0f мс, вход %s, уверенность %.2f: %r",
            spec.id,
            elapsed_ms,
            outcome.input_kind,
            outcome.mean_confidence,
            outcome.text,
        )
        text = outcome.text
        if decision.still_typing:
            # read before typing ended: the last word is likely cut, it comes with the rest
            text = drop_last_word(text)
        verdict = (spec.junk or self._junk).check(text, outcome.mean_confidence)
        if not verdict.ok:
            log.debug("Зона %s: отброшено фильтром (%s)", spec.id, verdict.reason)
            return None

        now = self._clock.now()
        t_change = decision.t_change
        result = self._dedup.process(
            text,
            spec.id,
            was_empty=stabilizer.was_empty,
            now=now,
            repeat_cooldown_min=spec.repeat_cooldown_min,
        )
        if result.used_was_empty:
            stabilizer.consume_was_empty()
        log.debug("Зона %s: dedup -> %s", spec.id, result.kind)
        if not result.voiced:
            return None

        if result.kind is DedupKind.NEW:
            speaker = spec.speaker.resolve(result.text, self._name_candidate(zone))
            zone.speaker = speaker.name
            text, kind = speaker.text, UtteranceKind.NEW
        else:
            text, kind = result.text, UtteranceKind.CONTINUATION
        if not text:
            return None
        utterance_id = next(self._ids)
        log.debug(
            "LATENCY change id=%d t=%.3f detected=%.3f zone=%s",
            utterance_id,
            t_change,
            now,
            spec.id,
        )
        return Utterance(
            channel=spec.id,
            kind=kind,
            text=text,
            full_text=text,
            speaker=zone.speaker,
            t_change=t_change,
            t_detected=now,
            confidence=outcome.mean_confidence,
            ocr_input=str(outcome.input_kind),
            id=utterance_id,
        )

    def _name_candidate(self, zone: _ZoneState) -> str | None:
        """OCR of the speaker zone or of the name-colour part of the zone (TZ 4.5)."""
        spec, name = zone.spec, zone.spec.name
        if name is None or zone.frame is None or zone.crop is None:
            return None
        if name.rect is not None:
            image = crop(zone.frame, name.rect)
            mask = build_mask(image, name.mask)
            if mask_empty(mask, spec.stabilization.empty_ratio):
                return None
        else:
            if zone.name_mask is None:
                return None
            part = cut_out(zone.crop, zone.name_mask)
            if part is None:
                return None
            image, mask = part, build_mask(part, name.mask)
        outcome = (spec.runner or self._runner).run(image, mask, OcrInput.RAW)
        log.debug("Зона %s: имя %r (%.2f)", spec.id, outcome.text, outcome.mean_confidence)
        return outcome.text or None
