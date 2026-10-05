"""Manual reading: a hotkey zone or a selected area read once (TZ 4.9)."""

from __future__ import annotations

import numpy as np

from subvoicer.config.resources import short_words, speaker_stoplist
from subvoicer.core.models import Rect, UtteranceKind
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec
from subvoicer.sources.manual import SELECT_CHANNEL, ManualReader, selection_spec
from subvoicer.sources.zone_ocr import ZoneSpec
from tests.fakes.clock import FakeClock
from tests.fakes.ocr import FakeOcr, line


def frame() -> np.ndarray:
    image = np.full((200, 400, 3), 20, dtype=np.uint8)
    image[40:60, 20:300] = 255
    image[100:120, 20:300] = 255
    return image


def reader(ocr: FakeOcr) -> ManualReader:
    return ManualReader(OcrRunner(ocr), JunkFilter(short_words()), FakeClock(5.0))


def test_lines_are_joined_by_newline_and_read_every_time() -> None:
    rows = [
        line("Дорогой дневник,", bbox=(0, 0, 80, 10)),
        line("сегодня шёл дождь.", bbox=(0, 30, 80, 40)),
    ]
    ocr = FakeOcr([rows, rows])
    spec = selection_spec(Rect(0, 0, 1, 1))
    first = reader(ocr).read(spec, frame())
    assert [u.text for u in first] == ["Дорогой дневник,\nсегодня шёл дождь."]
    assert first[0].channel == SELECT_CHANNEL and first[0].kind is UtteranceKind.NEW
    assert first[0].speaker is None
    again = reader(ocr).read(spec, frame())  # no dedup: asked again, read again
    assert [u.text for u in again] == [first[0].text]


def test_hotkey_zone_splits_speakers() -> None:
    ocr = FakeOcr([[line("Ви: Кто ты? Джеки: Вылезай из машины.")]])
    spec = ZoneSpec(
        id="note",
        rect=Rect(0, 0, 1, 1),
        mask=MaskSpec(MaskPreset.ADAPTIVE),
        speaker=SpeakerResolver(SpeakerMode.PREFIX, speaker_stoplist()),
    )
    found = reader(ocr).read(spec, frame())
    assert [(u.speaker, u.text) for u in found] == [
        ("Ви", "Кто ты?"),
        ("Джеки", "Вылезай из машины."),
    ]
    assert len({u.id for u in found}) == 2


def test_junk_is_not_read() -> None:
    ocr = FakeOcr([[line("|||", 0.3)]] * 3)  # low confidence tries all three inputs
    assert reader(ocr).read(selection_spec(Rect(0, 0, 1, 1)), frame()) == []


def test_nothing_on_screen() -> None:
    ocr = FakeOcr([[]] * 3)
    assert reader(ocr).read(selection_spec(Rect(0, 0, 1, 1)), frame()) == []
