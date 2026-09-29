"""Silero TTS v5 (``v5_cis_base``), CPU. Expects stress marks (``+``) in the text."""

from __future__ import annotations

import importlib.util
import logging
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import numpy as np

from subvoicer.core.errors import EngineError
from subvoicer.core.models import AudioClip, Gender, TtsCapabilities, Voice

log = logging.getLogger(__name__)

PAUSE = "…"  # TextPreparer marks pauses with a single ellipsis character
PAUSE_SSML = '<break time="300ms"/>'

_load_lock = threading.Lock()


class SileroEngine:
    id = "silero"
    name = "silero"
    display_name = "Silero v5"
    capabilities = TtsCapabilities(supports_rate=True, streaming=False, needs_gpu=False)

    def __init__(
        self,
        model: str = "v5_cis_base",
        language: str = "ru",
        sample_rate: int = 48_000,
        threads: int = 4,
        catalog: Mapping[str, Voice] | None = None,
    ) -> None:
        """``catalog``: gender and display name per voice id (``resources/voices``)."""
        self.model_id = model
        self.language = language
        self.sample_rate = sample_rate
        self._threads = threads
        self._catalog = dict(catalog or {})
        self._model: Any = None
        self._voices: list[Voice] = []

    @classmethod
    def is_available(cls) -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("silero", "torch"))

    def load(self) -> None:
        import torch

        torch.set_num_threads(self._threads)
        torch.set_grad_enabled(False)
        try:
            self._model = self._load_model()
        except Exception as exc:
            raise EngineError(f"Silero не загрузился: {exc}") from exc
        prefix = f"{self.language}_"
        ids = sorted(s for s in self._model.speakers if s.startswith(prefix))
        if not ids:
            raise EngineError(f"В модели {self.model_id} нет голосов {prefix}*")
        self._voices = [
            self._catalog.get(i, Voice(i, Gender.UNKNOWN, i.removeprefix(prefix).capitalize()))
            for i in ids
        ]
        self.synthesize("Прив+ет.", ids[0], 1.0)  # warm-up: the first call is ~20x slower
        log.info("Silero загружен: %s, голосов %d", self.model_id, len(ids))

    def close(self) -> None:
        self._model = None

    def voices(self) -> list[Voice]:
        return list(self._voices)

    def synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip:
        if self._model is None:
            raise EngineError("Silero не загружен: вызовите load()")
        if voice_id not in {v.id for v in self._voices}:
            raise EngineError(f"Silero: нет голоса {voice_id!r}")
        if rate == 1.0 and PAUSE not in text:
            audio = self._model.apply_tts(text=text, speaker=voice_id, sample_rate=self.sample_rate)
        else:
            audio = self._model.apply_tts(
                ssml_text=to_ssml(text, rate), speaker=voice_id, sample_rate=self.sample_rate
            )
        samples = np.asarray(audio.numpy(), dtype=np.float32)
        return AudioClip(samples, self.sample_rate)

    def _load_model(self) -> Any:
        """The package file is loaded directly when present; the first download goes through
        ``silero_tts``, which also fetches ``latest_silero_models.yml`` into the current
        directory — so that call runs in a temporary directory."""
        import silero
        from torch import package

        path = Path(silero.__file__).parent / "model" / f"{self.model_id}.pt"
        if path.is_file():
            return package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        from silero import silero_tts

        with _load_lock, tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                model, _ = silero_tts(language=self.language, speaker=self.model_id)
            finally:
                os.chdir(previous)
        return model


def to_ssml(text: str, rate: float) -> str:
    body = PAUSE_SSML.join(escape(part) for part in text.split(PAUSE))
    return f'<speak><prosody rate="{round(rate * 100)}%">{body}</prosody></speak>'
