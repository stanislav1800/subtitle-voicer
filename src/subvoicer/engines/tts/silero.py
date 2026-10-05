"""Silero TTS v5, CPU: one or more models behind one voice list (TZ 4.8, 1.19).

``v5_cis_base`` — 29 voices ``ru_*``; ``v5_5_ru`` — 5 better voices, exposed as ``v55_<speaker>``
(the prefix picks the model). Both take SSML ``<prosody rate>`` and respect ``+`` stress marks,
so the text comes prepared the same way for any voice.
"""

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
VOICE_PREFIX = {"v5_cis_base": "", "v5_5_ru": "v55_"}  # model -> prefix of its voice ids
SPEAKER_PREFIX = {"v5_cis_base": "ru_"}  # only the Russian speakers of a multi-language model

_load_lock = threading.Lock()


def voice_id(model_id: str, speaker: str) -> str:
    return VOICE_PREFIX.get(model_id, f"{model_id}_") + speaker


class SileroEngine:
    id = "silero"
    name = "silero"
    display_name = "Silero v5"
    capabilities = TtsCapabilities(supports_rate=True, streaming=False, needs_gpu=False)

    def __init__(
        self,
        models: Mapping[str, str | None] | None = None,
        language: str = "ru",
        sample_rate: int = 48_000,
        threads: int = 4,
        catalog: Mapping[str, Voice] | None = None,
    ) -> None:
        """``models``: model id -> the package file downloaded by the app into ``models/``
        (TZ 4.14), or None to use the ``silero`` package cache or its own download (tools).
        ``catalog``: gender, display name and ``preferred`` per voice id (``resources/voices``).
        """
        self.models = dict(models or {"v5_cis_base": None})
        self.language = language
        self.sample_rate = sample_rate
        self._threads = threads
        self._catalog = dict(catalog or {})
        self._loaded: dict[str, Any] = {}
        self._speaker: dict[str, tuple[Any, str]] = {}  # voice id -> (model, speaker)
        self._voices: list[Voice] = []

    @property
    def model_id(self) -> str:
        return ", ".join(self.models)

    @classmethod
    def is_available(cls) -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("silero", "torch"))

    def load(self) -> None:
        import torch

        torch.set_num_threads(self._threads)
        torch.set_grad_enabled(False)
        for model_id, path in self.models.items():
            try:
                model = self._load_model(model_id, path)
            except Exception as exc:
                raise EngineError(f"Silero {model_id} не загрузился: {exc}") from exc
            only = SPEAKER_PREFIX.get(model_id, "")
            speakers = sorted(s for s in model.speakers if s.startswith(only))
            if not speakers:
                raise EngineError(f"В модели {model_id} нет голосов {only}*")
            self._loaded[model_id] = model
            for speaker in speakers:
                vid = voice_id(model_id, speaker)
                self._speaker[vid] = (model, speaker)
                name = speaker.removeprefix(only).capitalize()
                self._voices.append(self._catalog.get(vid, Voice(vid, Gender.UNKNOWN, name)))
            first = voice_id(model_id, speakers[0])
            self.synthesize("Прив+ет.", first, 1.0)  # warm-up: the first call is ~20x slower
            log.info("Silero загружен: %s, голосов %d", model_id, len(speakers))

    def close(self) -> None:
        self._loaded.clear()
        self._speaker.clear()

    def voices(self) -> list[Voice]:
        return list(self._voices)

    def synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip:
        if not self._loaded:
            raise EngineError("Silero не загружен: вызовите load()")
        if voice_id not in self._speaker:
            raise EngineError(f"Silero: нет голоса {voice_id!r}")
        model, speaker = self._speaker[voice_id]
        if rate == 1.0 and PAUSE not in text:
            audio = model.apply_tts(text=text, speaker=speaker, sample_rate=self.sample_rate)
        else:
            audio = model.apply_tts(
                ssml_text=to_ssml(text, rate), speaker=speaker, sample_rate=self.sample_rate
            )
        samples = np.asarray(audio.numpy(), dtype=np.float32)
        return AudioClip(samples, self.sample_rate)

    def _load_model(self, model_id: str, model_path: str | None) -> Any:
        """The package file is loaded directly when present; the first download goes through
        ``silero_tts``, which also fetches ``latest_silero_models.yml`` into the current
        directory — so that call runs in a temporary directory."""
        import silero
        from torch import package

        path = Path(model_path or Path(silero.__file__).parent / "model" / f"{model_id}.pt")
        if model_path is not None and not path.is_file():
            raise EngineError(f"Нет файла модели {path}")
        if path.is_file():
            return package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        from silero import silero_tts

        with _load_lock, tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                model, _ = silero_tts(language=self.language, speaker=model_id)
            finally:
                os.chdir(previous)
        return model


def to_ssml(text: str, rate: float) -> str:
    body = PAUSE_SSML.join(escape(part) for part in text.split(PAUSE))
    return f'<speak><prosody rate="{round(rate * 100)}%">{body}</prosody></speak>'
