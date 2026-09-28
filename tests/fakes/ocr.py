"""Scripted OcrEngine: returns prepared results in order and records the inputs it saw."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import numpy.typing as npt

from subvoicer.core.models import OcrLine, OcrResult


def line(
    text: str, confidence: float = 0.95, bbox: tuple[int, int, int, int] = (0, 0, 10, 10)
) -> OcrLine:
    return OcrLine(text, confidence, bbox)


class FakeOcr:
    def __init__(self, results: Iterable[OcrResult | list[OcrLine]]) -> None:
        self._results = [r if isinstance(r, OcrResult) else OcrResult(tuple(r)) for r in results]
        self.inputs: list[npt.NDArray[np.uint8]] = []

    def recognize(self, image: npt.NDArray[np.uint8]) -> OcrResult:
        self.inputs.append(image)
        if not self._results:
            raise AssertionError("FakeOcr: no more scripted results")
        return self._results.pop(0)
