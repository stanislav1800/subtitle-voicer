"""Character voices (stage 16, TZ 1.21): the TTS wrapper and the helper-process client."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pytest

from subvoicer.core.errors import EngineError
from subvoicer.core.events import EngineUnavailable
from subvoicer.core.models import AudioClip, CharacterVoice, Gender
from subvoicer.engines.tts.character import CharacterVoices, matched
from subvoicer.engines.tts.convert_client import HostConverter
from tests.fakes.audio import FakeTts
from tests.fakes.capture import RecordingBus

FAKE_HOST = Path(__file__).parents[2] / "fixtures" / "convert" / "fake_host.py"
VI = CharacterVoice("char_vi", "Ви", Gender.FEMALE, "v2", "rvc", "vi", (("key", -3.0),))
GERALT = CharacterVoice("char_geralt", "Геральт", Gender.MALE, "v1", "svc", "geralt")


class LoudTts(FakeTts):
    def synthesize(self, text: str, voice_id: str, rate: float) -> AudioClip:
        self.calls.append((text, voice_id, rate))
        return AudioClip(np.full(480, 0.1, dtype=np.float32), 48_000)


class FakeConverter:
    def __init__(self, fail_load: bool = False, fail_convert: bool = False) -> None:
        self.loaded: list[tuple[str, str]] = []
        self.converted: list[tuple[str, str, dict[str, float | str]]] = []
        self.closed = 0
        self.fail_load, self.fail_convert = fail_load, fail_convert
        self.release = threading.Event()
        self.release.set()

    def load(self, kind: str, model: str) -> None:
        self.release.wait(5)
        if self.fail_load:
            raise EngineError("Голоса персонажей: нет видеокарты")
        self.loaded.append((kind, model))

    def convert(
        self, clip: AudioClip, kind: str, model: str, params: Mapping[str, float | str]
    ) -> AudioClip:
        if self.fail_convert:
            raise EngineError("Голоса персонажей: процесс завершился")
        self.converted.append((kind, model, dict(params)))
        return AudioClip(clip.samples * 5, 40_000)  # louder: the wrapper matches the base back

    def close(self) -> None:
        self.closed += 1


def wait_ready(voices: CharacterVoices, models: int) -> None:
    deadline = time.monotonic() + 5
    while len(voices._ready) < models and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(voices._ready) == models


def test_not_installed_adds_nothing_and_speaks_the_base_voice() -> None:
    tts = LoudTts()
    voices = CharacterVoices(tts, [VI], None, enabled=True)
    assert [v.id for v in voices.voices()] == ["v1", "v2"]
    clip = voices.synthesize("Привет.", "char_vi", 1.2)
    assert tts.calls == [("Привет.", "v2", 1.2)]  # the base voice of Ви
    assert clip.sample_rate == 48_000 and not clip.cacheable
    assert voices.synthesize("Привет.", "v1", 1.0).cacheable  # other voices untouched


def test_installed_voices_are_listed_with_the_mark_even_when_off() -> None:
    """An assigned voice must not vanish from a drop-down; with the setting off nothing runs."""
    converter = FakeConverter()
    voices = CharacterVoices(LoudTts(), [VI, GERALT], converter, enabled=False)
    listed = {v.id: v for v in voices.voices()}
    assert listed["char_vi"].character and listed["char_vi"].display_name == "Ви"
    assert listed["char_geralt"].gender is Gender.MALE
    assert not listed["v1"].character
    assert not voices.synthesize("Привет.", "char_vi", 1.0).cacheable
    assert converter.loaded == [] and converter.converted == []  # GPU never touched


def test_enabled_loads_ahead_and_converts_with_the_chosen_parameters() -> None:
    converter = FakeConverter()
    voices = CharacterVoices(LoudTts(), [VI, GERALT], converter, enabled=True)
    wait_ready(voices, 2)
    assert sorted(converter.loaded) == [("rvc", "vi"), ("svc", "geralt")]
    clip = voices.synthesize("Привет.", "char_vi", 1.0)
    assert converter.converted == [("rvc", "vi", {"key": -3.0})]
    assert clip.cacheable and clip.sample_rate == 40_000
    assert np.sqrt(np.mean(clip.samples**2)) == pytest.approx(0.1, rel=1e-3)  # base loudness


def test_until_the_model_is_loaded_the_line_is_the_base_voice() -> None:
    converter = FakeConverter()
    converter.release.clear()  # loading hangs
    voices = CharacterVoices(LoudTts(), [VI], converter, enabled=True)
    clip = voices.synthesize("Привет.", "char_vi", 1.0)
    assert not clip.cacheable and converter.converted == []
    converter.release.set()
    wait_ready(voices, 1)
    assert voices.synthesize("Привет.", "char_vi", 1.0).cacheable


def test_a_failure_is_reported_once_and_falls_back_to_the_base_voice() -> None:
    bus = RecordingBus()
    converter = FakeConverter(fail_convert=True)
    voices = CharacterVoices(LoudTts(), [VI], converter, bus, enabled=True)
    wait_ready(voices, 1)
    for _ in range(3):
        assert not voices.synthesize("Привет.", "char_vi", 1.0).cacheable
    (event,) = bus.of(EngineUnavailable)
    assert event.family == "characters" and "завершился" in event.reason  # type: ignore[attr-defined]


def test_a_failed_load_is_reported() -> None:
    bus = RecordingBus()
    voices = CharacterVoices(LoudTts(), [VI], FakeConverter(fail_load=True), bus, enabled=True)
    deadline = time.monotonic() + 5
    while not bus.of(EngineUnavailable) and time.monotonic() < deadline:
        time.sleep(0.01)
    (event,) = bus.of(EngineUnavailable)
    assert "видеокарты" in event.reason  # type: ignore[attr-defined]
    assert not voices.synthesize("Привет.", "char_vi", 1.0).cacheable


def test_turning_off_stops_the_helper_and_close_stops_it_too() -> None:
    converter = FakeConverter()
    voices = CharacterVoices(LoudTts(), [VI], converter, enabled=True)
    wait_ready(voices, 1)
    voices.set_enabled(False)
    assert converter.closed == 1 and not voices.active
    assert not voices.synthesize("Привет.", "char_vi", 1.0).cacheable
    voices.close()
    assert converter.closed == 2


def test_matched_keeps_peaks_below_full_scale() -> None:
    loud = np.array([0.0, 2.0, -2.0, 0.0], dtype=np.float32)
    out = matched(loud, np.array([0.0, 0.9, -0.9, 0.0], dtype=np.float32), gain_db=6)
    assert float(np.abs(out).max()) == pytest.approx(0.99)
    quiet = matched(np.zeros(4, np.float32), np.ones(4, np.float32))
    assert not quiet.any()  # silence stays silence


# ---------------------------------------------------------------------- the helper protocol


@pytest.fixture
def host(tmp_path: Path):  # type: ignore[no-untyped-def]
    converter = HostConverter(
        Path(sys.executable), tmp_path / "models", tmp_path / "host.log", FAKE_HOST
    )
    yield converter
    converter.close()


def test_protocol_round_trip(host: HostConverter, tmp_path: Path) -> None:
    assert host.ping()["device"] == "Fake GPU"
    host.load("rvc", "vi")
    samples = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    clip = host.convert(AudioClip(samples, 48_000), "rvc", "vi", {"key": -3.0, "f0": "dio"})
    assert clip.sample_rate == 96_000
    np.testing.assert_allclose(clip.samples, samples * 0.5)


def test_helper_error_is_an_engine_error_and_the_helper_keeps_running(host: HostConverter) -> None:
    with pytest.raises(EngineError, match="модель сломана"):
        host.load("rvc", "broken")
    assert host.ping()["ok"]


def test_a_dead_helper_is_restarted_on_the_next_request(host: HostConverter) -> None:
    with pytest.raises(EngineError, match="завершился"):
        host._call({"op": "exit"})
    assert host.ping()["device"] == "Fake GPU"


def test_not_installed_helper_is_an_engine_error(tmp_path: Path) -> None:
    converter = HostConverter(tmp_path / "env" / "python.exe", tmp_path)
    with pytest.raises(EngineError, match="не установлены"):
        converter.ping()


def test_only_installed_voices_are_listed_and_converted() -> None:
    """TZ 1.24: voices are downloaded one by one; a removed one sounds as its base voice."""
    converter = FakeConverter()
    voices = CharacterVoices(LoudTts(), [VI, GERALT], converter, enabled=True, installed=["vi"])
    wait_ready(voices, 1)
    assert [v.id for v in voices.voices() if v.character] == ["char_vi"]
    assert converter.loaded == [("rvc", "vi")]
    assert not voices.synthesize("Привет.", "char_geralt", 1.0).cacheable  # not installed
    voices.set_installed(["vi", "geralt"])
    wait_ready(voices, 2)
    assert {v.id for v in voices.voices() if v.character} == {"char_vi", "char_geralt"}
    assert voices.synthesize("Привет.", "char_geralt", 1.0).cacheable
    voices.set_installed([])
    assert not voices.installed and not [v for v in voices.voices() if v.character]
    assert not voices.synthesize("Привет.", "char_vi", 1.0).cacheable


class PingingConverter(FakeConverter):
    def __init__(self, device: str) -> None:
        super().__init__()
        self.device = device

    def ping(self) -> dict[str, str]:
        return {"device": self.device}


@pytest.mark.parametrize(
    ("built", "device", "told"),
    [("rocm", "cpu", True), ("cuda", "cpu", True), ("rocm", "AMD Radeon RX 7800 XT", False)],
)
def test_an_engine_for_the_gpu_on_the_cpu_is_told_once(built: str, device: str, told: bool) -> None:
    """TZ 1.25: e.g. an old AMD driver — the voices work, but slowly; the user should know."""
    bus = RecordingBus()
    voices = CharacterVoices(LoudTts(), [VI], PingingConverter(device), bus, True, gpu=built)
    wait_ready(voices, 1)
    events = bus.of(EngineUnavailable)
    assert len(events) == int(told)
    if told:
        assert events[0].family == "characters_cpu"  # type: ignore[attr-defined]
        assert ("Adrenalin" in events[0].reason) == (built == "rocm")  # type: ignore[attr-defined]


def test_an_engine_for_the_cpu_says_nothing() -> None:
    bus = RecordingBus()
    voices = CharacterVoices(LoudTts(), [VI], PingingConverter("cpu"), bus, True, gpu=None)
    wait_ready(voices, 1)
    assert bus.of(EngineUnavailable) == []
