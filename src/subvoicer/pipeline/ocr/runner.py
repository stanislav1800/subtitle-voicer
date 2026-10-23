"""Running an OcrEngine over a zone: input variants, low-confidence retries, line order (TZ 4.3)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from subvoicer.core.models import OcrLine, OcrResult
from subvoicer.core.ports import OcrEngine
from subvoicer.pipeline.ocr.inputs import Image, OcrInput, build_input

JUDGE_SLACK = 0.05  # a word more or less known is not «worse words»
SAME_ROW_OVERLAP = 0.5  # boxes overlapping vertically by half of the lower one are one row


@dataclass(frozen=True, slots=True)
class OcrSettings:
    upscale: float = 2.0
    min_line_height: int = 20  # by measure on real frames (DECISIONS, TZ 1.17)
    low_confidence: float = 0.8


@dataclass(frozen=True, slots=True)
class OcrAttempt:
    kind: OcrInput
    lines: tuple[OcrLine, ...]  # in reading order, bboxes in crop pixels
    mean_confidence: float


@dataclass(frozen=True, slots=True)
class OcrOutcome:
    text: str
    best: OcrAttempt
    attempts: tuple[OcrAttempt, ...]  # in the order they were run

    @property
    def mean_confidence(self) -> float:
        return self.best.mean_confidence

    @property
    def input_kind(self) -> OcrInput:
        return self.best.kind


class OcrRunner:
    def __init__(
        self,
        engine: OcrEngine,
        settings: OcrSettings | None = None,
        judge: Callable[[str], float] | None = None,
    ) -> None:
        """``judge``: how much a text looks like real words, 0..1 (``WordFixer.known_share``);
        a fallback input then wins only if its words are not worse (``better``)."""
        self._engine = engine
        self._settings = settings or OcrSettings()
        self._judge = judge

    def attempt(self, crop: Image, mask: Image, kind: OcrInput) -> OcrAttempt:
        """Run OCR once on one input variant."""
        prepared = build_input(
            crop,
            mask,
            kind,
            upscale=self._settings.upscale,
            min_line_height=self._settings.min_line_height,
        )
        result = self._engine.recognize(prepared.image)
        lines = tuple(_rescale(line, prepared.scale) for line in reading_order(result))
        return OcrAttempt(kind, lines, mean_confidence(lines))

    def run(
        self,
        crop: Image,
        mask: Image,
        default: OcrInput,
        *,
        separator: str = " ",
    ) -> OcrOutcome:
        """OCR with ``default`` input; on low confidence also try the other two variants.

        ``separator`` joins text lines: a space for ``auto`` zones, a newline for manual reading.
        """
        attempts = [self.attempt(crop, mask, default)]
        if attempts[0].mean_confidence < self._settings.low_confidence:
            for kind in OcrInput:
                if kind is not default:
                    attempts.append(self.attempt(crop, mask, kind))
        best = attempts[0]
        for other in attempts[1:]:
            if self.better(other, best):
                best = other
        text = separator.join(line.text for line in best.lines)
        return OcrOutcome(text, best, tuple(attempts))

    def better(self, other: OcrAttempt, best: OcrAttempt) -> bool:
        """A higher confidence wins (the first on ties). A fallback input (masked, mask) loses
        thin letters of a colour mask, yet the engine is sure of what is left: 13 of 14 such
        wins in a Cyberpunk letsplay were garbled («Слушои ч о э им людям», DECISIONS
        2026-10-23) — so with a ``judge`` it must also read words no worse."""
        if other.mean_confidence <= best.mean_confidence:
            return False
        if self._judge is None or not any(ch.isalpha() for ch in _text(best)):
            return True
        return self._judge(_text(other)) >= self._judge(_text(best)) - JUDGE_SLACK


def _text(attempt: OcrAttempt) -> str:
    return " ".join(line.text for line in attempt.lines)


def mean_confidence(lines: Sequence[OcrLine]) -> float:
    if not lines:
        return 0.0
    return sum(line.confidence for line in lines) / len(lines)


def reading_order(result: OcrResult) -> list[OcrLine]:
    """Top to bottom; within one row, left to right."""
    rows: list[list[OcrLine]] = []
    for line in sorted(result.lines, key=lambda ln: (ln.bbox[1] + ln.bbox[3]) / 2):
        if rows and _same_row(rows[-1], line):
            rows[-1].append(line)
        else:
            rows.append([line])
    ordered: list[OcrLine] = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda ln: ln.bbox[0]))
    return [line for line in ordered if line.text.strip()]


def _same_row(row: list[OcrLine], line: OcrLine) -> bool:
    top = min(ln.bbox[1] for ln in row)
    bottom = max(ln.bbox[3] for ln in row)
    overlap = min(bottom, line.bbox[3]) - max(top, line.bbox[1])
    height = min(bottom - top, line.bbox[3] - line.bbox[1])
    return height > 0 and overlap >= SAME_ROW_OVERLAP * height


def _rescale(line: OcrLine, scale: float) -> OcrLine:
    if scale == 1.0:
        return line
    x1, y1, x2, y2 = (round(v / scale) for v in line.bbox)
    return OcrLine(line.text, line.confidence, (x1, y1, x2, y2))
