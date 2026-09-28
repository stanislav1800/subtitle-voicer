"""RapidOCR (onnxruntime, CPU) with a PP-OCRv5 mobile recognition model."""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

import numpy as np
import numpy.typing as npt

from subvoicer.core.errors import EngineError
from subvoicer.core.models import OcrCapabilities, OcrLine, OcrResult

log = logging.getLogger(__name__)

MODELS = ("cyrillic", "eslav")


class RapidOcrEngine:
    id = "rapidocr"
    display_name = "RapidOCR (PP-OCRv5)"
    capabilities = OcrCapabilities(languages=("ru",), returns_confidence=True)

    def __init__(self, model: str = "cyrillic", threads: int = 4, det_max_side: int = 1600) -> None:
        """``model``: recognition model, one of MODELS.

        ``det_max_side``: the detector downscales images whose longer side exceeds this and
        never upscales (RapidOCR's default upscales the short side to 736 px, which makes a
        1200x120 subtitle crop ~7x slower).
        """
        if model not in MODELS:
            raise ValueError(f"Unknown RapidOCR model {model!r}, expected one of {MODELS}")
        self.model = model
        self._threads = threads
        self._det_max_side = det_max_side
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
                    "Det.limit_type": "max",
                    "Det.limit_side_len": self._det_max_side,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.lang_type": lang,
                    "Rec.model_type": ModelType.MOBILE,
                    "EngineConfig.onnxruntime.intra_op_num_threads": self._threads,
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
        result = self._engine(image)
        if result.txts is None or result.boxes is None or result.scores is None:
            return OcrResult(())
        lines = []
        for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
            xs, ys = box[:, 0], box[:, 1]
            bbox = (int(xs.min()), int(ys.min()), int(np.ceil(xs.max())), int(np.ceil(ys.max())))
            lines.append(OcrLine(str(text), float(score), bbox))
        return OcrResult(tuple(lines))
