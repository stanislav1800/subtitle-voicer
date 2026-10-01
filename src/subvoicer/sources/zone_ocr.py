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
from subvoicer.pipeline.ocr.runner import OcrOutcome, OcrRunner
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
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
            found += self._process_zone(zone, frame, now)
        return found

    def _process_zone(self, zone: _ZoneState, frame: Frame, now: float) -> list[Utterance]:
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
            return []
        assert isinstance(decision, RunOcr)
        return self._recognize(zone, decision)

    def _recognize(self, zone: _ZoneState, decision: RunOcr) -> list[Utterance]:
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
        text = self._keep_prefix_name(spec, outcome, text=outcome.text)
        if decision.still_typing:
            # read before typing ended: the last word is likely cut, it comes with the rest
            text = drop_last_word(text)
        verdict = (spec.junk or self._junk).check(text, outcome.mean_confidence)
        if not verdict.ok:
            log.debug("Зона %s: отброшено фильтром (%s)", spec.id, verdict.reason)
            return []

        now = self._clock.now()
        t_change = decision.t_change
        was_empty = stabilizer.was_empty
        if stabilizer.flickered and self._dedup.same_as_last(text, spec.id):
            # the subtitle blinked off for a moment: the same line, not a new one (TZ 1.17)
            log.debug("Зона %s: мигание %.0f мс — та же реплика", spec.id, stabilizer.empty_ms)
            was_empty = False
            stabilizer.consume_was_empty()
        result = self._dedup.process(
            text,
            spec.id,
            was_empty=was_empty,
            now=now,
            repeat_cooldown_min=spec.repeat_cooldown_min,
        )
        if result.used_was_empty:
            stabilizer.consume_was_empty()
        log.debug("Зона %s: dedup -> %s", spec.id, result.kind)
        if not result.voiced:
            return []

        lines: list[tuple[UtteranceKind, str | None, str]] = []
        if result.kind is DedupKind.NEW:
            for speaker in spec.speaker.resolve_all(result.text, self._name_candidate(zone)):
                lines.append((UtteranceKind.NEW, speaker.name, speaker.text))
        else:  # the head continues the last speaker, inner names start new lines (TZ 1.17)
            for name, part in spec.speaker.split_inner(result.text):
                if name is None:
                    lines.append((UtteranceKind.CONTINUATION, zone.speaker, part))
                else:
                    lines.append((UtteranceKind.NEW, name, part))
        utterances = []
        for kind, speaker_name, text in lines:
            if not text:
                continue
            zone.speaker = speaker_name
            utterance_id = next(self._ids)
            log.debug(
                "LATENCY change id=%d t=%.3f detected=%.3f zone=%s",
                utterance_id,
                t_change,
                now,
                spec.id,
            )
            utterances.append(
                Utterance(
                    channel=spec.id,
                    kind=kind,
                    text=text,
                    full_text=text,
                    speaker=speaker_name,
                    t_change=t_change,
                    t_detected=now,
                    confidence=outcome.mean_confidence,
                    ocr_input=str(outcome.input_kind),
                    id=utterance_id,
                )
            )
        return utterances

    @staticmethod
    def _keep_prefix_name(spec: ZoneSpec, outcome: OcrOutcome, text: str) -> str:
        """A fallback input (masked / mask) loses a name of another colour; the default
        input still has it: put it back in front (TZ 1.17)."""
        default = outcome.attempts[0]
        if spec.speaker.mode is not SpeakerMode.PREFIX or outcome.best is default:
            return text
        if spec.speaker.resolve(text).name is not None:
            return text
        named = spec.speaker.resolve(" ".join(line.text for line in default.lines))
        if named.name is None:
            return text
        return f"{named.name}: {text.lstrip(':;,. ')}"

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
