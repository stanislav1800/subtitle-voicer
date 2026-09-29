"""SAPI5 (Windows system voices) fallback TTS, used when Silero is not available."""

from __future__ import annotations

import importlib.util
import logging
import math
import sys
import threading
from typing import Any

import numpy as np

from subvoicer.core.errors import EngineError
from subvoicer.core.models import AudioClip, Gender, TtsCapabilities, Voice

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
SAFT_48KHZ_16BIT_MONO = 38  # SpeechAudioFormatType
RATE_STEP = 1.11  # one SAPI rate unit is ~11% faster (measured: +2 -> x1.24, +4 -> x1.54)
LANGUAGE_RU = "419"


class Sapi5Engine:
    id = "sapi5"
    name = "sapi5"
    display_name = "Голоса Windows (SAPI5)"
    capabilities = TtsCapabilities(supports_rate=True, streaming=False, needs_gpu=False)

    def __init__(self, language: str = LANGUAGE_RU) -> None:
        self.language = language
        self._voices: list[Voice] = []
        self._local = threading.local()  # COM objects belong to the thread that made them

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "win32" and importlib.util.find_spec("comtypes") is not None

    def load(self) -> None:
        tokens = self._speaker().GetVoices(f"Language={self.language}")
        self._voices = []
        for i in range(tokens.Count):
            token = tokens.Item(i)
            gender = {"Male": Gender.MALE, "Female": Gender.FEMALE}.get(
                token.GetAttribute("Gender"), Gender.UNKNOWN
            )
            self._voices.append(Voice(token.Id, gender, token.GetDescription()))
        if not self._voices:
            raise EngineError("SAPI5: нет системных голосов для русского языка")
        log.info("SAPI5: голосов %d", len(self._voices))

    def close(self) -> None:
        self._local = threading.local()

    def voices(self) -> list[Voice]:
        return list(self._voices)

    def synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip:
        import comtypes.client as cc

        speaker = self._speaker()
        tokens = speaker.GetVoices(f"Language={self.language}")
        token = next(
            (tokens.Item(i) for i in range(tokens.Count) if tokens.Item(i).Id == voice_id), None
        )
        if token is None:
            raise EngineError(f"SAPI5: нет голоса {voice_id!r}")
        stream = cc.CreateObject("SAPI.SpMemoryStream")
        audio_format = cc.CreateObject("SAPI.SpAudioFormat")
        audio_format.Type = SAFT_48KHZ_16BIT_MONO
        stream.Format = audio_format
        speaker.AudioOutputStream = stream
        speaker.Voice = token
        speaker.Rate = max(-10, min(10, round(math.log(rate) / math.log(RATE_STEP))))
        speaker.Speak(text.replace("+", ""), 0)  # SAPI does not understand Silero stresses
        pcm = np.frombuffer(bytes(stream.GetData()), dtype="<i2")
        return AudioClip((pcm / 32768).astype(np.float32), SAMPLE_RATE)

    def _speaker(self) -> Any:
        speaker = getattr(self._local, "speaker", None)
        if speaker is None:
            import comtypes
            import comtypes.client as cc

            comtypes.CoInitialize()
            speaker = cc.CreateObject("SAPI.SpVoice")
            self._local.speaker = speaker
        return speaker
