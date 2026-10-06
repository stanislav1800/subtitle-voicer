"""Character voices (stage 16, TZ 1.21): a TTS engine plus timbre conversion.

``CharacterVoices`` wraps the loaded engine. While the setting ``character_voices`` is on and
the helper is installed, their models are loaded ahead in a background thread. Installed
character voices (``resources/voices/characters.toml``) are listed next to the engine's ones
whether the setting is on or not — an assigned voice must not vanish from a drop-down. A line in
such a voice is synthesized by the base voice and converted; until the model is ready, with the
setting off or on any failure it is the base voice — marked not cacheable, so the real voice
replaces it later. When the setting is off nothing is started and the GPU is not touched.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

import numpy as np

from subvoicer.core.errors import EngineError
from subvoicer.core.events import EngineUnavailable
from subvoicer.core.models import AudioClip, CharacterVoice, Voice
from subvoicer.core.ports import EventPublisher, TtsEngine, VoiceConverter

log = logging.getLogger(__name__)

FAMILY = "characters"  # EngineUnavailable.family: the UI shows it once per failure


class CharacterVoices:
    def __init__(
        self,
        base: TtsEngine,
        voices: Sequence[CharacterVoice],
        converter: VoiceConverter | None,
        publisher: EventPublisher | None = None,
        enabled: bool = False,
    ) -> None:
        """``converter`` is None when the helper is not installed."""
        self.base = base
        self.name = getattr(base, "name", "tts")
        self._voices = {v.id: v for v in voices}
        self._converter = converter
        self._publisher = publisher
        self._lock = threading.Lock()
        self._ready: set[str] = set()  # models loaded in the helper
        self._generation = 0  # a newer enable/disable cancels an older warm-up
        self._failed = False  # reported once until the next enable
        self._enabled = False
        self.set_enabled(enabled)

    @property
    def active(self) -> bool:
        return self._enabled and self._converter is not None

    @property
    def installed(self) -> bool:
        return self._converter is not None

    def set_enabled(self, enabled: bool) -> None:
        """On: start the helper and load the models in the background. Off: stop the helper,
        which frees the video memory."""
        with self._lock:
            if enabled == self._enabled:
                return
            self._enabled = enabled
            self._generation += 1
            self._ready.clear()
            self._failed = False
            generation = self._generation
        if self._converter is None:
            return
        if enabled:
            threading.Thread(
                target=self._warm_up, args=(generation,), name="CharacterVoices", daemon=True
            ).start()
        else:
            self._converter.close()

    def voices(self) -> list[Voice]:
        own = self.base.voices()
        if self._converter is None:
            return own
        return own + [Voice(v.id, v.gender, v.name, character=True) for v in self._voices.values()]

    def is_character(self, voice_id: str) -> bool:
        return voice_id in self._voices

    def synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip:
        voice = self._voices.get(voice_id)
        if voice is None:
            return self.base.synthesize(text, voice_id, rate)
        base = self.base.synthesize(text, voice.base, rate)
        stand_in = AudioClip(base.samples, base.sample_rate, cacheable=False)
        with self._lock:
            ready = self.active and voice.model in self._ready
            generation = self._generation
        if not ready or self._converter is None:
            return stand_in
        try:
            converted = self._converter.convert(base, voice.kind, voice.model, dict(voice.params))
        except EngineError as exc:
            self._report(generation, str(exc))
            return stand_in
        return AudioClip(
            matched(converted.samples, base.samples, voice.gain_db), converted.sample_rate
        )

    def close(self) -> None:
        with self._lock:
            self._generation += 1
            self._enabled = False
        if self._converter is not None:
            self._converter.close()

    # ------------------------------------------------------------------ internals

    def _warm_up(self, generation: int) -> None:
        assert self._converter is not None
        for model, kind in sorted({(v.model, v.kind) for v in self._voices.values()}):
            with self._lock:
                if generation != self._generation:
                    return
            try:
                self._converter.load(kind, model)
            except EngineError as exc:
                self._report(generation, str(exc))
                return
            with self._lock:
                if generation != self._generation:
                    return
                self._ready.add(model)
            log.info("Голос персонажа готов: %s", model)

    def _report(self, generation: int, reason: str) -> None:
        with self._lock:
            if generation != self._generation or self._failed:
                return
            self._failed = True
            self._ready.clear()
        log.warning("%s — звучат голоса-основы", reason)
        if self._publisher is not None:
            self._publisher.publish(EngineUnavailable(FAMILY, "convert_host", reason))


def matched(samples: np.ndarray, reference: np.ndarray, gain_db: float = 0.0) -> np.ndarray:
    """``samples`` as loud as ``reference`` (RMS), then ``gain_db``; peaks kept below full scale."""
    out = samples.astype(np.float32)
    level = float(np.sqrt(np.mean(out**2))) if len(out) else 0.0
    target = float(np.sqrt(np.mean(reference.astype(np.float32) ** 2))) if len(reference) else 0.0
    if level > 1e-6 and target > 0:
        out = out * (target / level)
    out = out * np.float32(10 ** (gain_db / 20))
    peak = float(np.abs(out).max()) if len(out) else 0.0
    if peak > 0.99:
        out = out * np.float32(0.99 / peak)
    return out.astype(np.float32)
