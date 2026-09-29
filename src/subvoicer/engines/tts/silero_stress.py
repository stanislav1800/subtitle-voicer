"""StressMarker via ``silero-stress``: puts ``+`` before stressed vowels, solves homographs.

Checked on silero-stress 1.5: words that already contain ``+`` are left untouched, even
when the accentor would choose another syllable ("м+олоко" stays), so manual stresses from
the pronunciation dictionary need no placeholders (docs/DECISIONS.md).
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

from subvoicer.core.errors import EngineError

log = logging.getLogger(__name__)


class SileroStressMarker:
    id = "silero_stress"
    display_name = "silero-stress"

    def __init__(self, language: str = "ru") -> None:
        self.language = language
        self._accentor: Any = None

    @classmethod
    def is_available(cls) -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("silero_stress", "torch"))

    def load(self) -> None:
        from silero_stress import load_accentor

        self._accentor = load_accentor(self.language)
        if self._accentor is None:
            raise EngineError(f"silero-stress не поддерживает язык {self.language!r}")
        log.info("silero-stress загружен")

    def close(self) -> None:
        self._accentor = None

    def mark(self, text: str) -> str:
        if self._accentor is None:
            raise EngineError("silero-stress не загружен: вызовите load()")
        return str(self._accentor(text))
