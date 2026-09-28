"""Windows.Media.Ocr (WinRT) fallback engine, language ru."""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import sys
from typing import Any

import numpy as np
import numpy.typing as npt

from subvoicer.core.errors import EngineError
from subvoicer.core.models import OcrCapabilities, OcrLine, OcrResult

log = logging.getLogger(__name__)

LANGUAGE = "ru"

_runtime_preloaded = False


def preload_msvc_runtime() -> None:
    """Load the system msvcp140.dll before the first ``winrt`` import.

    The winrt wheels ship an old msvcp140.dll (14.29). Whichever copy is loaded first is
    reused by the whole process, and with the old one ``import onnxruntime`` afterwards
    crashes with an access violation. The system copy (VC++ redistributable) is newer.
    """
    global _runtime_preloaded
    if _runtime_preloaded or sys.platform != "win32":
        return
    _runtime_preloaded = True
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    path = os.path.join(system_root, "System32", "msvcp140.dll")
    try:
        ctypes.WinDLL(path)
    except OSError:
        log.warning("Не удалось заранее загрузить %s; OCR RapidOCR может упасть после WinRT", path)


class WinRtOcrEngine:
    id = "winrt"
    display_name = "Windows OCR"
    # Windows.Media.Ocr gives no confidence; lines report 1.0
    capabilities = OcrCapabilities(languages=(LANGUAGE,), returns_confidence=False)

    def __init__(self) -> None:
        self._engine: Any = None

    @classmethod
    def is_available(cls) -> bool:
        if sys.platform != "win32" or importlib.util.find_spec("winrt") is None:
            return False
        preload_msvc_runtime()
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine

            return bool(OcrEngine.is_language_supported(Language(LANGUAGE)))
        except Exception:
            return False

    def load(self) -> None:
        preload_msvc_runtime()
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        engine = OcrEngine.try_create_from_language(Language(LANGUAGE))
        if engine is None:
            raise EngineError("Windows OCR: русский язык распознавания не установлен")
        self._engine = engine
        log.info("Windows OCR загружен (язык %s)", LANGUAGE)

    def close(self) -> None:
        self._engine = None

    def recognize(self, image: npt.NDArray[np.uint8]) -> OcrResult:
        if self._engine is None:
            raise EngineError("Windows OCR не загружен: вызовите load()")
        import cv2
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap

        if image.ndim == 2:
            bgra = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
        elif image.shape[2] == 3:
            bgra = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
        else:
            bgra = np.ascontiguousarray(image)
        height, width = bgra.shape[:2]
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            memoryview(bgra.tobytes()), BitmapPixelFormat.BGRA8, width, height
        )
        result = self._engine.recognize_async(bitmap).get()
        lines = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            x1 = min(w.bounding_rect.x for w in words)
            y1 = min(w.bounding_rect.y for w in words)
            x2 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
            y2 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
            bbox = (int(x1), int(y1), int(np.ceil(x2)), int(np.ceil(y2)))
            lines.append(OcrLine(str(line.text), 1.0, bbox))
        return OcrResult(tuple(lines))
