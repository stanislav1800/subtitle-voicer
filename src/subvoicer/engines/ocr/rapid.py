"""RapidOCR (onnxruntime, CPU) with a PP-OCRv5 mobile recognition model."""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from subvoicer.core.errors import EngineError
from subvoicer.core.models import OcrCapabilities, OcrLine, OcrResult

log = logging.getLogger(__name__)

MODELS = ("cyrillic", "eslav")

Box = tuple[int, int, int, int]  # x1, y1, x2, y2

SAME_ROW_OVERLAP = 0.5  # boxes overlapping vertically by half of the lower one share a row
MAX_ROW_GAP = 2.0  # boxes of one row further apart than 2 row heights stay separate
LINE_PADDING = 0.15  # padding around a merged line, in row heights


class RapidOcrEngine:
    id = "rapidocr"
    display_name = "RapidOCR (PP-OCRv5)"
    capabilities = OcrCapabilities(languages=("ru",), returns_confidence=True)

    def __init__(
        self,
        model: str = "eslav",
        threads: int = 4,
        det_max_side: int = 1280,
        merge_rows: bool = True,
        det_path: str | None = None,
        rec_path: str | None = None,
    ) -> None:
        """``model``: recognition model, one of MODELS.

        ``det_path`` / ``rec_path``: model files downloaded by the app into ``models/``
        (TZ 4.14); without them RapidOCR uses its own cache (tools, benchmarks).

        ``det_max_side``: images whose longer side exceeds this are downscaled for the
        detector only; text lines are still cut from the full-size image for the recogniser.
        Detection cost grows with the pixel count, so an input the pipeline upscaled x2 for
        small text would otherwise be ~4x slower to detect, while the recogniser resizes each
        line to 48 px height anyway. RapidOCR's own resizing is switched off: its default
        upscales the short side to 736 px, and with ``limit_type = max`` it substitutes its
        own limit (960 / 1500 / 2000 by image size).

        ``merge_rows``: the detector splits a subtitle line into overlapping word-sized
        boxes; recognising them separately doubles letters at the seams and loses the line
        context (Latin look-alikes: "Ha" instead of "На"). With ``merge_rows`` the boxes of
        one row are merged and the recogniser reads the whole line.
        """
        if model not in MODELS:
            raise ValueError(f"Unknown RapidOCR model {model!r}, expected one of {MODELS}")
        self.model = model
        self._threads = threads
        self._det_max_side = det_max_side
        self._merge_rows = merge_rows
        self._paths = {"Det.model_path": det_path, "Rec.model_path": rec_path}
        self._engine: Any = None

    @classmethod
    def is_available(cls) -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("rapidocr", "onnxruntime"))

    def load(self) -> None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        lang = {"cyrillic": LangRec.CYRILLIC, "eslav": LangRec.ESLAV}[self.model]
        try:
            self._engine = RapidOCR(
                params={
                    "Global.use_cls": False,  # subtitles are never rotated
                    "Global.log_level": "warning",
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.MOBILE,
                    # "min" with 32: no resizing beyond rounding to a multiple of 32
                    "Det.limit_type": "min",
                    "Det.limit_side_len": 32,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.lang_type": lang,
                    "Rec.model_type": ModelType.MOBILE,
                    "EngineConfig.onnxruntime.intra_op_num_threads": self._threads,
                    **{key: path for key, path in self._paths.items() if path},
                }
            )
        except Exception as exc:
            raise EngineError(f"RapidOCR не загрузился: {exc}") from exc
        log.info("RapidOCR загружен: PP-OCRv5 mobile, модель %s", self.model)

    def close(self) -> None:
        self._engine = None

    def recognize(self, image: npt.NDArray[np.uint8]) -> OcrResult:
        if self._engine is None:
            raise EngineError("RapidOCR не загружен: вызовите load()")
        if image.ndim == 3 and image.shape[2] == 4:
            # RapidOCR treats 4 channels as RGBA; our frames are BGRA
            image = np.ascontiguousarray(image[:, :, :3])
        if not self._merge_rows:
            result = self._engine(image, use_det=True, use_cls=False, use_rec=True)
            if result.txts is None or result.boxes is None or result.scores is None:
                return OcrResult(())
            return OcrResult(
                tuple(
                    OcrLine(str(text), float(score), _bbox(box))
                    for box, text, score in zip(
                        result.boxes, result.txts, result.scores, strict=True
                    )
                )
            )

        height, width = image.shape[:2]
        det_scale = min(1.0, self._det_max_side / max(height, width))
        det_image: Any = image
        if det_scale < 1.0:
            det_image = cv2.resize(
                image, None, fx=det_scale, fy=det_scale, interpolation=cv2.INTER_AREA
            )
        detected = self._engine(det_image, use_det=True, use_cls=False, use_rec=False)
        if detected.boxes is None or len(detected.boxes) == 0:
            return OcrResult(())
        boxes = [_scale(_bbox(box), 1 / det_scale) for box in detected.boxes]
        lines = []
        for x1, y1, x2, y2 in merge_rows(boxes):
            pad = max(2, round(LINE_PADDING * (y2 - y1)))
            box = (max(x1 - pad, 0), max(y1 - pad, 0), min(x2 + pad, width), min(y2 + pad, height))
            crop = np.ascontiguousarray(image[box[1] : box[3], box[0] : box[2]])
            rec = self._engine(crop, use_det=False, use_cls=False, use_rec=True)
            if rec.txts and rec.scores is not None and str(rec.txts[0]).strip():
                lines.append(OcrLine(str(rec.txts[0]), float(rec.scores[0]), box))
        return OcrResult(tuple(lines))

    def detect_lines(self, image: npt.NDArray[np.uint8], max_side: int = 800) -> list[Box]:
        """Text lines of ``image`` by the detector alone, no recognition (TZ 4.2.2): the
        subtitle plates are looked for around them. The detector works on a copy shrunk to
        ``max_side`` — as accurate on the plates of real frames as 1280 px and 2x faster
        (DECISIONS.md, stage 13); the boxes are in pixels of ``image``."""
        if self._engine is None:
            raise EngineError("RapidOCR не загружен: вызовите load()")
        image = np.ascontiguousarray(image[:, :, :3])
        height, width = image.shape[:2]
        scale = min(1.0, max_side / max(height, width))
        small = image
        if scale < 1.0:
            small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        detected = self._engine(small, use_det=True, use_cls=False, use_rec=False)
        if detected.boxes is None or len(detected.boxes) == 0:
            return []
        return merge_rows([_scale(_bbox(box), 1 / scale) for box in detected.boxes])


def merge_rows(boxes: list[Box]) -> list[Box]:
    """Merge boxes into rows by vertical overlap; split a row where the gap is too wide."""
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        if rows:
            current = _union(rows[-1])
            overlap = min(current[3], box[3]) - max(current[1], box[1])
            lower = min(current[3] - current[1], box[3] - box[1])
            if lower > 0 and overlap >= SAME_ROW_OVERLAP * lower:
                rows[-1].append(box)
                continue
        rows.append([box])

    merged: list[Box] = []
    for row in rows:
        segment: list[Box] = []
        top, bottom = _union(row)[1], _union(row)[3]
        row_height = bottom - top
        for box in sorted(row, key=lambda b: b[0]):
            if segment and box[0] - _union(segment)[2] > MAX_ROW_GAP * row_height:
                merged.append(_union(segment))
                segment = []
            segment.append(box)
        merged.append(_union(segment))
    return merged


def _union(boxes: list[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _scale(box: Box, factor: float) -> Box:
    if factor == 1.0:
        return box
    x1, y1, x2, y2 = box
    return (
        int(x1 * factor),
        int(y1 * factor),
        int(np.ceil(x2 * factor)),
        int(np.ceil(y2 * factor)),
    )


def _bbox(points: Any) -> Box:
    xs, ys = points[:, 0], points[:, 1]
    return int(xs.min()), int(ys.min()), int(np.ceil(xs.max())), int(np.ceil(ys.max()))
