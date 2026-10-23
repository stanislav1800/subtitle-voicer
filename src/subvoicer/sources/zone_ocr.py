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
from dataclasses import dataclass, field, replace
from typing import Literal

from subvoicer.core.models import Frame, OcrLine, Rect, Utterance, UtteranceKind
from subvoicer.core.ports import Clock, TextDetector
from subvoicer.pipeline.dedup import (
    SPLIT_WINDOW_S,
    DedupKind,
    Deduplicator,
    continues,
    grown_from,
    on_screen,
)
from subvoicer.pipeline.ocr.colour import MIN_DENSITY, colour_lines, density, rows_of
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrOutcome, OcrRunner, mean_confidence
from subvoicer.pipeline.ocr.words import WordFixer
from subvoicer.pipeline.speaker.resolver import (
    BRACKET_NAME,
    SpeakerMode,
    SpeakerResolver,
    SpeakerResult,
)
from subvoicer.pipeline.text.latin import twins_to_cyrillic
from subvoicer.pipeline.text.skip import SkipList
from subvoicer.pipeline.vision.change import mask_empty
from subvoicer.pipeline.vision.crop import crop
from subvoicer.pipeline.vision.masks import HsvRange, Image, MaskPreset, MaskSpec, build_mask
from subvoicer.pipeline.vision.plates import (
    Box,
    Plate,
    PlateSettings,
    cut_short,
    find_plates,
    plate_mask,
    still_there,
)
from subvoicer.pipeline.vision.regions import (
    cut_out,
    enlarged,
    first_line,
    letters_only,
    name_above,
    name_before,
    paint_out,
    without,
)
from subvoicer.pipeline.vision.stability import (
    Emptied,
    RunOcr,
    StabilizationSettings,
    ZoneStabilizer,
)

log = logging.getLogger(__name__)
latency = logging.getLogger("subvoicer.latency")  # tools/latency_report.py


def drop_last_word(text: str) -> str:
    """``"Охрана на входе сме"`` -> ``"Охрана на входе"``; a single word is kept."""
    head, _, _ = text.rstrip().rpartition(" ")
    return head.rstrip() if head.strip() else text.strip()


@dataclass(frozen=True)
class NameSource:
    """Where the speaker candidate is read: a speaker zone (``rect`` + its mask; a colour
    mask reads only that colour there) or, with ``rect`` None, the pixels of the name colour
    inside the text zone, ``place`` — before the first text row or on a row above it."""

    mask: MaskSpec
    rect: Rect | None = None
    place: Literal["before", "above"] = "before"


@dataclass(frozen=True)
class PlateSearch:
    """A «Субтитры (авто)» zone (TZ 4.2.2): its rect is where subtitle plates are looked
    for; they are looked for again every ``locate_ms`` and soon when one is gone."""

    detector: TextDetector
    settings: PlateSettings = field(default_factory=PlateSettings)
    locate_ms: int = 300
    min_locate_ms: int = 100  # a vanished plate does not make it search every frame
    # plates just gone are looked for every min_locate_ms for this long: the detector now
    # and then misses a plate for one search — the miss must be shorter than the flicker
    # rule (300 ms, TZ 1.17), or the same line is voiced again
    recheck_ms: int = 1000


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
    words: WordFixer | None = None  # fixing OCR words by the dictionary (TZ 1.23)
    plates: PlateSearch | None = None  # look for subtitle plates in rect (TZ 4.2.2)
    skip: SkipList | None = None  # «Не озвучивать» of the game (TZ 4.5, 1.33)
    # the name's colour (TZ 1.36): its rows are not dropped as of another colour, a name in
    # brackets of another colour is not a name
    name_colours: tuple[HsvRange, ...] = ()


@dataclass
class _ZoneState:
    spec: ZoneSpec
    stabilizer: ZoneStabilizer
    frame_ts: float | None = None
    frame: Image | None = None
    crop: Image | None = None
    mask: Image | None = None
    name_mask: Image | None = None  # name colour pixels (speaker mode color)
    watch: Image | None = None  # what the stabiliser compares: a colour mask without blobs
    speaker: str | None = None  # speaker of the last new line (a continuation keeps it)
    shown: list[str] = field(default_factory=list)  # lines of the last reading (TZ 1.22)
    voiced: str = ""  # the last voiced text and when (a split line's second half, TZ 1.28)
    voiced_at: float = float("-inf")
    found: list[Plate] = field(default_factory=list)  # plates of a plates zone (TZ 4.2.2)
    located_at: float = float("-inf")
    seen_at: float = float("-inf")  # plates were last found then
    # the first row OCR read (crop pixels): the name colour is looked for left of it (TZ 4.5)
    first_row: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class ZoneReading:
    """One read of a zone without the stabiliser and dedup: the editor preview now,
    hotkey zones on stage 7."""

    crop: Image
    mask: Image
    text: str
    confidence: float
    input_kind: str
    elapsed_ms: float
    lines: list[SpeakerResult]  # per speaker (TZ 1.17), names already taken out
    plates: list[tuple[int, int, int, int]] = field(default_factory=list)  # TZ 4.2.2, in crop


def locate(image: Image, search: PlateSearch) -> list[Plate]:
    """Subtitle plates of a zone's crop (TZ 4.2.2).

    The detector on the whole search area now and then misses a line of a tight two-line
    plate (Alan Wake: the lower line is gone at 800 and 1280 px, found at 960) — the plate
    then ends at the first line and the second one comes and goes, voiced again each time.
    So it looks again at a strip around a plate with letters at its edge (``cut_short``),
    where it sees both lines."""
    lines = search.detector.detect_lines(image)
    plates = find_plates(image, lines, search.settings)
    height, width = image.shape[:2]
    extra: list[Box] = []
    for plate in plates:
        if not cut_short(image, plate, search.settings):
            continue
        x1, y1, x2, y2 = plate.box
        reach = max(b[3] - b[1] for b in plate.lines)
        left, top = max(0, x1 - reach), max(0, y1 - reach)
        strip = image[top : min(height, y2 + reach), left : min(width, x2 + reach)]
        for bx1, by1, bx2, by2 in search.detector.detect_lines(strip):
            box = (bx1 + left, by1 + top, bx2 + left, by2 + top)
            if bx2 - bx1 < search.settings.min_aspect * (by2 - by1):
                continue  # a key hint by the plate («F5»), not a line of it
            if not any(_covers(known, box) for known in [*lines, *extra]):
                extra.append(box)
    return find_plates(image, [*lines, *extra], search.settings) if extra else plates


def _covers(known: Box, box: Box) -> bool:
    """Most of ``box`` is a line already known."""
    common = max(0, min(known[2], box[2]) - max(known[0], box[0])) * max(
        0, min(known[3], box[3]) - max(known[1], box[1])
    )
    return common >= 0.5 * max(1, (box[2] - box[0]) * (box[3] - box[1]))


def fixed_words(spec: ZoneSpec, text: str) -> str:
    """TZ 1.23: OCR words fixed by the dictionary, before the junk filter and dedup; Latin
    twins first become Cyrillic, or a short «HeT» was dropped as not Cyrillic."""
    text = twins_to_cyrillic(text)
    return spec.words.fix(text) if spec.words is not None else text


def in_colour(
    spec: ZoneSpec, outcome: OcrOutcome, image: Image, mask: Image, separator: str = " "
) -> tuple[str, float]:
    """A zone with the text found by colour (TZ 4.3, 1.35, 1.37): rows with few pixels of
    the colour ``mask`` are not its text; the text and the confidence of the lines left."""
    lines, _ = in_colour_lines(spec, outcome, image, mask)
    if [line.text for line in lines] == [line.text for line in outcome.best.lines]:
        return outcome.text, outcome.mean_confidence
    return separator.join(line.text for line in lines), mean_confidence(lines)


def in_colour_lines(
    spec: ZoneSpec, outcome: OcrOutcome, image: Image, mask: Image
) -> tuple[list[OcrLine], tuple[int, int, int, int] | None]:
    """The lines of the zone's colour (all lines for other presets) and the box of their
    first row."""
    lines = list(outcome.best.lines)
    brackets = spec.speaker.mode is SpeakerMode.BRACKETS and bool(spec.name_colours)
    names = (
        build_mask(image, MaskSpec(MaskPreset.COLOR, colors_hsv=spec.name_colours))
        if brackets
        else None
    )
    if spec.mask.preset is MaskPreset.COLOR:
        lines, dropped = colour_lines(
            lines,
            mask,
            names,  # other modes paint names out of the text
            (lambda text: BRACKET_NAME.search(text) is not None) if brackets else None,
        )
        if dropped:
            log.debug("Зона %s: не того цвета: %r", spec.id, [line.text for line in dropped])
    if names is not None:
        lines = [_brackets_in_colour(spec, line, names) for line in lines]
    if not lines:
        return lines, None
    row = rows_of(lines)[0]
    box = (
        min(line.bbox[0] for line in row),
        min(line.bbox[1] for line in row),
        max(line.bbox[2] for line in row),
        max(line.bbox[3] for line in row),
    )
    return lines, box


def _brackets_in_colour(spec: ZoneSpec, line: OcrLine, names: Image) -> OcrLine:
    """«[ Имя ]» not of the name colour is text, not a name (TZ 1.36): its brackets go. The
    part of the line box it takes is told by its place among the line's characters."""
    text = line.text
    x1, y1, x2, y2 = line.bbox
    per_char = (x2 - x1) / max(1, len(text))
    cut: list[tuple[int, int]] = []
    for match in BRACKET_NAME.finditer(text):
        box = (
            int(x1 + match.start() * per_char),
            y1,
            int(x1 + match.end() * per_char),
            y2,
        )
        if density(names, [box]) < MIN_DENSITY:
            cut.append(match.span())
    if not cut:
        return line
    log.debug("Зона %s: скобки не цвета имени: %r", spec.id, [text[a:b] for a, b in cut])
    for start, end in reversed(cut):
        inner = BRACKET_NAME.match(text, start)
        assert inner is not None
        text = text[:start] + inner.group(1) + text[end:]
    return replace(line, text=text)


def skipped(spec: ZoneSpec, text: str) -> bool:
    """A text of the game's «Не озвучивать» list (TZ 1.33), with a DEBUG line why."""
    phrase = spec.skip.match(text) if spec.skip else None
    if phrase is not None:
        log.debug(
            "Зона %s: не озвучиваю — в списке «Не озвучивать» (%s): %r", spec.id, phrase, text
        )
    return phrase is not None


def read_once(spec: ZoneSpec, frame: Image, runner: OcrRunner, separator: str = " ") -> ZoneReading:
    state = _ZoneState(spec, ZoneStabilizer(spec.stabilization))
    state.frame = frame
    state.crop = crop(frame, spec.rect)
    if spec.plates is not None:
        state.found = locate(state.crop, spec.plates)
        state.mask = plate_mask(state.crop, state.found, spec.plates.settings)
    else:
        state.mask = build_mask(state.crop, spec.mask)
    if spec.name is not None and spec.name.rect is None:
        state.name_mask = build_mask(state.crop, spec.name.mask)
        state.mask = without(state.mask, state.name_mask)
    active = spec.runner or runner
    image = state.crop if state.name_mask is None else paint_out(state.crop, state.name_mask)
    started = time.perf_counter()
    if spec.plates is not None:
        texts, first, confidence = ZoneOcrSource._read_plates(state, image, active)
        text, kind = separator.join(texts), str(first.input_kind if first else spec.ocr_input)
    else:
        outcome = active.run(image, state.mask, spec.ocr_input, separator=separator)
        text, confidence = in_colour(spec, outcome, image, state.mask, separator)
        state.first_row = in_colour_lines(spec, outcome, image, state.mask)[1]
        text = fixed_words(spec, ZoneOcrSource._keep_prefix_name(spec, outcome, text))
        kind = str(outcome.input_kind)
    elapsed = (time.perf_counter() - started) * 1000
    candidate = ZoneOcrSource._read_name(state, active)
    lines = spec.speaker.resolve_all(text, candidate) if text else []
    return ZoneReading(
        state.crop,
        state.mask,
        text,
        confidence,
        kind,
        elapsed,
        lines,
        [p.box for p in state.found],
    )


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

    def plates(self) -> dict[str, list[tuple[float, float, float, float]]]:
        """Zone id -> the plates found, in fractions of the frame (developer overlay)."""
        found = {}
        for zone in self._zones:
            if zone.spec.plates is None or zone.crop is None:
                continue
            height, width = zone.crop.shape[:2]
            r = zone.spec.rect
            sx, sy = (r.x2 - r.x1) / width, (r.y2 - r.y1) / height
            found[zone.spec.id] = [
                (r.x1 + x1 * sx, r.y1 + y1 * sy, r.x1 + x2 * sx, r.y1 + y2 * sy)
                for x1, y1, x2, y2 in (p.box for p in zone.found)
            ]
        return found

    def states(self) -> dict[str, str]:
        """Zone id -> stabiliser state (developer overlay)."""
        return {zone.spec.id: str(zone.stabilizer.state.name) for zone in self._zones}

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
            if spec.plates is not None:
                self._locate(zone, spec.plates, frame.timestamp)
                zone.mask = plate_mask(zone.crop, zone.found, spec.plates.settings)
            else:
                zone.mask = build_mask(zone.crop, spec.mask)
            if spec.name is not None and spec.name.rect is None:
                zone.name_mask = build_mask(zone.crop, spec.name.mask)
                zone.mask = without(zone.mask, zone.name_mask)
            # patches of the scene in the text colour are not text changing (TZ 1.37)
            colour = spec.mask.preset is MaskPreset.COLOR and spec.plates is None
            zone.watch = letters_only(zone.mask) if colour else zone.mask
            event_time = frame.timestamp
        else:
            event_time = now  # no new frame: only time passes

        before = stabilizer.state
        area = None  # a plates zone: shares of the plates, the search area is far bigger
        if spec.plates is not None:
            area = sum((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in (p.box for p in zone.found))
        decision = stabilizer.update(
            zone.watch if zone.watch is not None else zone.mask, event_time, area
        )
        if stabilizer.state is not before:
            log.debug("Зона %s: %s -> %s", spec.id, before, stabilizer.state)
        if isinstance(decision, Emptied) or decision is None:
            return []
        assert isinstance(decision, RunOcr)
        return self._recognize(zone, decision)

    def _locate(self, zone: _ZoneState, search: PlateSearch, now: float) -> None:
        """Plates again every ``locate_ms``, and sooner when a plate is gone (TZ 4.2.2)."""
        assert zone.crop is not None
        since = (now - zone.located_at) * 1000
        gone = any(not still_there(zone.crop, p, search.settings) for p in zone.found)
        recheck = not zone.found and (now - zone.seen_at) * 1000 <= search.recheck_ms
        if not (since >= search.locate_ms or ((gone or recheck) and since >= search.min_locate_ms)):
            return
        zone.located_at = now
        before = [p.box for p in zone.found]
        zone.found = locate(zone.crop, search)
        if zone.found:
            zone.seen_at = now
        if [p.box for p in zone.found] != before:
            log.debug("Зона %s: подложки %s", zone.spec.id, [p.box for p in zone.found])

    @staticmethod
    def _read_plates(
        zone: _ZoneState, image: Image, runner: OcrRunner
    ) -> tuple[list[str], OcrOutcome | None, float]:
        """Every plate read on its own, top to bottom: its text (names kept, words fixed),
        the first outcome (for the log and the input kind) and the mean confidence."""
        assert zone.mask is not None
        spec = zone.spec
        texts, outcomes = [], []
        for plate in zone.found:
            x1, y1, x2, y2 = plate.box
            outcome = runner.run(image[y1:y2, x1:x2], zone.mask[y1:y2, x1:x2], spec.ocr_input)
            text = fixed_words(spec, ZoneOcrSource._keep_prefix_name(spec, outcome, outcome.text))
            if text.strip():
                texts.append(text)
                outcomes.append(outcome)
        if not outcomes:
            return [], None, 0.0
        weights = [max(1, len(t)) for t in texts]
        confidence = sum(o.mean_confidence * w for o, w in zip(outcomes, weights, strict=True))
        # a name in brackets over its line may be a plate of its own (TZ 1.33)
        return spec.speaker.glue_names(texts), outcomes[0], confidence / sum(weights)

    def _recognize(self, zone: _ZoneState, decision: RunOcr) -> list[Utterance]:
        spec, stabilizer = zone.spec, zone.stabilizer
        assert zone.crop is not None and zone.mask is not None
        runner = spec.runner or self._runner
        image = zone.crop if zone.name_mask is None else paint_out(zone.crop, zone.name_mask)
        started = time.perf_counter()
        plate_texts: list[str] = []  # a plates zone: one text per plate (TZ 4.2.2)
        first: OcrOutcome | None
        if spec.plates is not None:
            plate_texts, first, confidence = self._read_plates(zone, image, runner)
            text = " ".join(plate_texts)
        else:
            first = runner.run(image, zone.mask, spec.ocr_input)
            text, confidence = in_colour(spec, first, image, zone.mask)
            zone.first_row = in_colour_lines(spec, first, image, zone.mask)[1]
            text = fixed_words(spec, self._keep_prefix_name(spec, first, text=text))
        elapsed_ms = (time.perf_counter() - started) * 1000
        input_kind = str(first.input_kind) if first is not None else str(spec.ocr_input)
        log.debug(
            "Зона %s: OCR %.0f мс, вход %s, уверенность %.2f: %r",
            spec.id,
            elapsed_ms,
            input_kind,
            confidence,
            plate_texts or text,
        )
        # on-screen hints of the game's «Не озвучивать» list; a plates zone: plate by plate
        if plate_texts:
            plate_texts = [t for t in plate_texts if not skipped(spec, t)]
            if not plate_texts:
                return []
            text = " ".join(plate_texts)
        elif skipped(spec, text):
            return []
        if decision.still_typing:
            # read before typing ended: the last word is likely cut, it comes with the rest
            text = drop_last_word(text)
            if plate_texts:
                plate_texts[-1] = drop_last_word(plate_texts[-1])
        # the next line's name shows before its text: wait for the text (TZ 1.22)
        text = spec.speaker.without_trailing_name(text)
        if plate_texts:
            plate_texts[-1] = spec.speaker.without_trailing_name(plate_texts[-1])
        verdict = (spec.junk or self._junk).check(text, confidence)
        if not verdict.ok:
            log.debug(
                "Зона %s: не озвучиваю — фильтр мусора (%s, уверенность %.2f): %r",
                spec.id,
                verdict.reason,
                confidence,
                text,
            )
            return []

        # a plates zone: plates whose text is still on screen are not part of the new line
        # — dedup compares only the new plates (TZ 4.2.2: a short line over a long one)
        still: list[str] = []
        if len(plate_texts) > 1 and not stabilizer.was_empty:
            fresh = [t for t in plate_texts if not on_screen(t, zone.shown)]
            if not fresh:
                log.debug("Зона %s: не озвучиваю — все подложки ещё на экране", spec.id)
                return []
            still = [t for t in plate_texts if t not in fresh]
            plate_texts, text = fresh, " ".join(fresh)

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
        if not result.voiced:
            log.debug("Зона %s: не озвучиваю — повтор (%s): %r", spec.id, result.kind, text)
            return []
        log.debug("Зона %s: dedup -> %s", spec.id, result.kind)

        # TZ 1.22: lines of the previous reading that are still on screen are not voiced again
        previous = [] if was_empty else zone.shown
        candidate = self._name_candidate(zone)
        # every plate is a line of its own, as if one under another in one zone (4.2.2)
        readings = plate_texts if len(plate_texts) > 1 else [text]
        zone.shown = [p.text for t in readings for p in spec.speaker.parts(t, candidate)]
        zone.shown += [p.text for t in still for p in spec.speaker.parts(t)]
        lines: list[tuple[UtteranceKind, str | None, str]] = []
        speaker = spec.speaker
        new_text = result.text
        whole = [part for t in readings for part in speaker.parts(t, candidate)]
        # a stacked subtitle that does not start with a name (a line without a name above, or
        # the cut top of an older one), some of its lines already voiced: line by line too
        stacked = (
            result.kind is DedupKind.NEW
            and speaker.resolve(new_text).name is None
            and len(whole) > 1
            and any(on_screen(part.text, previous) for part in whole)
        )
        if stacked:
            for found in whole:
                lines.append((UtteranceKind.NEW, found.name, found.text))
        elif result.kind is DedupKind.CONTINUATION and len(whole) > 1:
            # another speaker's line appeared below: the tail cut by length may take letters of
            # the previous line or of the new name, so the whole reading goes line by line
            # (TZ 1.22) — a line that grew from a previous one gives only its new end
            for found in whole:
                grown = grown_from(found.text, previous)
                kind = UtteranceKind.NEW if grown is None else UtteranceKind.CONTINUATION
                name = found.name if grown is None or found.name else zone.speaker
                lines.append((kind, name, found.text if grown is None else grown))
        elif result.kind is DedupKind.NEW or speaker.resolve(new_text).name is not None:
            # a new line, or a grown part that starts with a name: that speaker's line
            for found in speaker.resolve_all(new_text, candidate):
                lines.append((UtteranceKind.NEW, found.name, found.text))
        else:  # the head continues the last speaker, inner names start new lines (TZ 1.17)
            for name, part in speaker.split_inner(new_text):
                if name is None:
                    lines.append((UtteranceKind.CONTINUATION, zone.speaker, part))
                else:
                    lines.append((UtteranceKind.NEW, name, part))
        kept = [line for line in lines if not on_screen(line[2], previous)]
        for line in lines:
            if line not in kept:
                log.debug("Зона %s: не озвучиваю — ещё на экране: %r", spec.id, line[2])
        lines = kept
        if (
            lines
            and lines[0][0] is UtteranceKind.NEW
            and lines[0][1] is None
            and now - zone.voiced_at <= SPLIT_WINDOW_S
            and continues(zone.voiced, lines[0][2])
        ):
            # the game split one line in two subtitles: the same voice, right after the first
            log.debug("Зона %s: вторая половина реплики: %r", spec.id, lines[0][2])
            lines[0] = (UtteranceKind.CONTINUATION, zone.speaker, lines[0][2])
        utterances = []
        for kind, speaker_name, text in lines:
            if not text or skipped(spec, text):
                continue
            zone.speaker = speaker_name
            zone.voiced, zone.voiced_at = text, now
            utterance_id = next(self._ids)
            latency.debug(
                "LATENCY change id=%d t=%.3f detected=%.3f ocr=%.0f zone=%s kind=%s text=%r",
                utterance_id,
                t_change,
                now,
                elapsed_ms,
                spec.id,
                kind.value,
                f"{speaker_name}: {text}" if speaker_name else text,
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
                    confidence=confidence,
                    ocr_input=input_kind,
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
        return self._read_name(zone, zone.spec.runner or self._runner)

    @staticmethod
    def _read_name(zone: _ZoneState, runner: OcrRunner) -> str | None:
        """OCR of the speaker zone or of the name-colour part of the zone (TZ 4.5, 1.36)."""
        spec, name = zone.spec, zone.spec.name
        if name is None or zone.frame is None or zone.crop is None:
            return None
        if name.rect is not None:
            image = crop(zone.frame, name.rect)
            mask = build_mask(image, name.mask)
            if mask_empty(mask, spec.stabilization.empty_ratio):
                return None
            image = enlarged(image)
            mask = build_mask(image, name.mask)
        else:
            part = ZoneOcrSource._name_part(zone, name.place)
            if part is None:
                return None
            part = enlarged(part)
            image, mask = part, build_mask(part, name.mask)
        outcome = runner.run(image, mask, OcrInput.RAW)
        text = outcome.text
        if name.mask.preset is MaskPreset.COLOR:
            # only the name colour is the name: something else in a name zone is not (1.36)
            kept, dropped = colour_lines(outcome.best.lines, mask, by_rows=False)
            if dropped:
                text = " ".join(line.text for line in kept)
                log.debug("Зона %s: в имени не того цвета: %r", spec.id, [d.text for d in dropped])
        log.debug("Зона %s: имя %r (%.2f)", spec.id, text, outcome.mean_confidence)
        return text or None

    @staticmethod
    def _name_part(zone: _ZoneState, place: str) -> Image | None:
        """The name-colour pixels before the first text row or on the row above it.

        The first row is the one OCR read (other colours left out): specks of the text colour
        above the subtitle threw off the text mask's first line, and the whole zone's red was
        then taken with a red HUD label («ОМЕГА-БЛОКАТОРЫ ДЖОННИ», 2026-10-23). Without OCR
        rows (a plates zone) the text mask's first line is used."""
        assert zone.crop is not None
        if zone.name_mask is None:
            return None
        line = zone.first_row
        if line is None and zone.mask is not None:
            line = first_line(zone.mask)
        if place == "above":
            return name_above(zone.crop, zone.name_mask, line) if line is not None else None
        if line is not None:
            part = name_before(zone.crop, zone.name_mask, line)
            if part is not None or zone.first_row is not None:
                return part  # nothing of the name colour before the row read: no name
        return cut_out(zone.crop, zone.name_mask)
