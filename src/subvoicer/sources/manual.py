"""Manual reading (TZ 4.9): a ``hotkey`` zone or an area selected with Ctrl+Alt+A, read once.

No stabiliser and no dedup: the user asked for it, so it is voiced even if it was voiced
before. Lines are joined with a newline to keep paragraphs (TZ 4.3).
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterable

from subvoicer.core.models import Rect, Utterance, UtteranceKind
from subvoicer.core.ports import Clock
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.vision.masks import Image, MaskPreset, MaskSpec
from subvoicer.sources.zone_ocr import ZoneSpec, read_once, skipped

log = logging.getLogger(__name__)

SELECT_CHANNEL = "select"  # the channel of «выделить и прочитать»
MANUAL_READ_ID_START = 3_000_000  # apart from zone lines, manual lines and repeats


def selection_spec(rect: Rect, stoplist: Iterable[str] = ()) -> ZoneSpec:
    """A selected area: any text, so the ``adaptive`` mask of the «Записка» template, no
    speaker."""
    return ZoneSpec(
        id=SELECT_CHANNEL,
        rect=rect,
        mask=MaskSpec(MaskPreset.ADAPTIVE),
        speaker=SpeakerResolver(SpeakerMode.NONE, stoplist),
    )


class ManualReader:
    def __init__(self, runner: OcrRunner, junk: JunkFilter, clock: Clock) -> None:
        self._runner = runner
        self._junk = junk
        self._clock = clock
        self._ids = itertools.count(MANUAL_READ_ID_START)

    def read(self, spec: ZoneSpec, frame: Image) -> list[Utterance]:
        """Utterances of the zone on ``frame``, one per speaker; empty if nothing readable."""
        started = self._clock.now()
        reading = read_once(spec, frame, spec.runner or self._runner, separator="\n")
        verdict = (spec.junk or self._junk).check(reading.text, reading.confidence)
        if not reading.text or not verdict.ok:
            log.info(
                "Зона %s: нечего читать (%s, %r)",
                spec.id,
                verdict.reason or "пусто",
                reading.text,
            )
            return []
        now = self._clock.now()
        log.info("Зона %s прочитана по запросу: %r", spec.id, reading.text)
        return [
            Utterance(
                channel=spec.id,
                kind=UtteranceKind.NEW,
                text=line.text,
                full_text=reading.text,
                speaker=line.name,
                t_change=started,
                t_detected=now,
                confidence=reading.confidence,
                ocr_input=reading.input_kind,
                id=next(self._ids),
            )
            for line in reading.lines
            if line.text.strip() and not skipped(spec, line.text)
        ]
