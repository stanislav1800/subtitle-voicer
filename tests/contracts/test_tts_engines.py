"""TtsEngine contract, run for every registered engine available on this machine."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest

from subvoicer.core.errors import EngineError
from subvoicer.engines import registry

pytestmark = [pytest.mark.models, pytest.mark.windows]

TEXT = "Прив+ет! Встр+етимся у мост+а."


def engine_ids() -> list[str]:
    return [eid for eid in registry.entries("tts") if registry.resolve("tts", eid).is_available()]


@pytest.fixture(scope="module", params=engine_ids())
def engine(request: pytest.FixtureRequest) -> Iterator[object]:
    instance = registry.resolve("tts", request.param)()
    try:
        instance.load()
    except EngineError as exc:
        pytest.skip(f"{request.param}: {exc}")
    yield instance
    instance.close()


def test_metadata_and_voices(engine: object) -> None:
    assert isinstance(engine.id, str) and isinstance(engine.display_name, str)  # type: ignore[attr-defined]
    voices = engine.voices()  # type: ignore[attr-defined]
    assert voices
    assert len({v.id for v in voices}) == len(voices)


def test_synthesizes_float32_audio(engine: object) -> None:
    voice = engine.voices()[0].id  # type: ignore[attr-defined]
    clip = engine.synthesize(TEXT, voice, 1.0)  # type: ignore[attr-defined]
    assert clip.samples.dtype == np.float32
    assert clip.sample_rate == 48_000
    assert 0.5 < clip.duration < 10
    assert np.abs(clip.samples).max() > 0.01


def test_unknown_voice_is_a_clear_error(engine: object) -> None:
    with pytest.raises(EngineError):
        engine.synthesize(TEXT, "no-such-voice", 1.0)  # type: ignore[attr-defined]


def test_rate_changes_duration(engine: object) -> None:
    if not engine.capabilities.supports_rate:  # type: ignore[attr-defined]
        pytest.skip("движок не меняет скорость")
    voice = engine.voices()[0].id  # type: ignore[attr-defined]
    normal = engine.synthesize(TEXT, voice, 1.0).duration  # type: ignore[attr-defined]
    fast = engine.synthesize(TEXT, voice, 1.3).duration  # type: ignore[attr-defined]
    assert fast < normal * 0.9


def test_pause_marker(engine: object) -> None:
    voice = engine.voices()[0].id  # type: ignore[attr-defined]
    plain = engine.synthesize("Раз. Два.", voice, 1.0).duration  # type: ignore[attr-defined]
    paused = engine.synthesize("Раз … Два.", voice, 1.0).duration  # type: ignore[attr-defined]
    assert paused > 0.3 * plain
