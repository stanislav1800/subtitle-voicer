"""Zone source end to end on fakes: the stage 2 criteria that do not need a real game."""

from __future__ import annotations

import numpy as np
import pytest

from subvoicer.config.resources import short_words, speaker_stoplist
from subvoicer.core.models import Frame, OcrLine, OcrResult, Rect, Utterance, UtteranceKind
from subvoicer.pipeline.dedup import Deduplicator
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec
from subvoicer.sources.zone_ocr import ZoneOcrSource, ZoneSpec
from tests.fakes.clock import FakeClock

H, W = 80, 400
FRAME_S = 0.1  # poll_hz = 10


class ScriptedOcr:
    """Returns the text currently 'drawn', so OCR follows what the screen shows."""

    def __init__(self) -> None:
        self.text = ""
        self.calls = 0

    def recognize(self, image: np.ndarray) -> OcrResult:
        self.calls += 1
        if not self.text:
            return OcrResult(())
        return OcrResult((OcrLine(self.text, 0.97, (0, 0, 10, 10)),))


class Screen:
    def __init__(self, speaker: SpeakerMode = SpeakerMode.NONE) -> None:
        self.clock = FakeClock(100.0)
        self.ocr = ScriptedOcr()
        zone = ZoneSpec(
            id="dialog",
            rect=Rect(0.0, 0.0, 1.0, 1.0),
            mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
            speaker=SpeakerResolver(speaker, speaker_stoplist()),
        )
        self.source = ZoneOcrSource(
            [zone], OcrRunner(self.ocr), JunkFilter(short_words()), Deduplicator(), self.clock
        )
        self.frame: Frame | None = None
        self.out: list[Utterance] = []

    def show(self, text: str, *, typed: int | None = None, background: int = 20) -> None:
        """Draw ``text`` (``typed`` characters of it) as a white block per character."""
        image = np.full((H, W, 4), background, dtype=np.uint8)
        visible = text if typed is None else text[:typed]
        for i, _ in enumerate(visible):
            x = 5 + (i % 60) * 6
            y = 10 + (i // 60) * 30
            image[y : y + 20, x : x + 4, :3] = 255
        self.ocr.text = visible
        self.frame = Frame(image, self.clock.now())

    def tick(self, seconds: float = FRAME_S, *, new_frame: bool = True) -> None:
        """Advance time; by default the window sends a new frame with the same picture."""
        self.clock.advance(seconds)
        assert self.frame is not None
        if new_frame:
            self.frame = Frame(self.frame.image, self.clock.now())
        self.out += self.source.process(self.frame)

    def run(self, seconds: float) -> None:
        for _ in range(round(seconds / FRAME_S)):
            self.tick()


def test_line_is_printed_once_and_unchanged_subtitle_is_not_recognised_again() -> None:
    s = Screen()
    s.show("Встретимся у моста через пять минут.")
    s.run(5)
    assert [u.text for u in s.out] == ["Встретимся у моста через пять минут."]
    assert s.ocr.calls == 1


def test_typewriter_text_is_one_line() -> None:
    s = Screen()
    line = "Не нравится мне это место, давай уйдём."
    for typed in range(1, len(line) + 1, 3):  # 3 characters per frame, ~1.3 s
        s.show(line, typed=typed)
        s.tick()
    s.show(line)
    s.run(1)
    assert [u.text for u in s.out] == [line]
    assert s.out[0].kind is UtteranceKind.NEW


def test_long_typewriter_text_continues_instead_of_repeating() -> None:
    s = Screen()
    line = "Слушай внимательно, я не буду повторять дважды, потому что времени у нас совсем нет."
    for typed in range(1, len(line) + 1, 2):  # 2 characters per frame: longer than max_wait
        s.show(line, typed=typed)
        s.tick()
    s.show(line)
    s.run(1)
    assert s.out[0].kind is UtteranceKind.NEW
    assert all(u.kind is UtteranceKind.CONTINUATION for u in s.out[1:])
    spoken = " ".join(u.text for u in s.out)
    assert spoken.replace(" ", "") == line.replace(" ", "")


def test_same_line_twice_through_empty_zone_is_printed_twice() -> None:
    s = Screen()
    for _ in range(2):
        s.show("Да.")
        s.run(1)
        s.show("")
        s.run(0.5)
    assert [u.text for u in s.out] == ["Да.", "Да."]


def test_hint_with_colon_is_printed_whole_without_a_name() -> None:
    s = Screen(SpeakerMode.PREFIX)
    s.show("Совет: нажмите Q, чтобы уклониться.")
    s.run(1)
    assert len(s.out) == 1
    assert s.out[0].speaker is None
    assert s.out[0].text == "Совет: нажмите Q, чтобы уклониться."


def test_speaker_prefix_and_continuation_keeps_speaker() -> None:
    s = Screen(SpeakerMode.PREFIX)
    s.show("Джуди: Ты вовремя.")
    s.run(1)
    s.show("Джуди: Ты вовремя. Заходи, покажу кое-что.")
    s.run(1)
    assert [(u.kind, u.speaker, u.text) for u in s.out] == [
        (UtteranceKind.NEW, "Джуди", "Ты вовремя."),
        (UtteranceKind.CONTINUATION, "Джуди", "Заходи, покажу кое-что."),
    ]


def test_static_window_without_new_frames_still_settles() -> None:
    """WGC sends no frames for an unchanging window: time alone must trigger OCR."""
    s = Screen()
    s.show("Здесь никого нет.")
    for _ in range(5):
        s.tick(new_frame=False)
    assert [u.text for u in s.out] == ["Здесь никого нет."]


def test_new_line_replaces_old_without_empty_zone() -> None:
    s = Screen()
    s.show("Встретимся у моста.")
    s.run(1)
    s.show("Мост перекрыт, едем в объезд через доки.")
    s.run(1)
    assert [u.text for u in s.out] == [
        "Встретимся у моста.",
        "Мост перекрыт, едем в объезд через доки.",
    ]


def test_junk_is_dropped_and_zone_keeps_was_empty() -> None:
    s = Screen()
    s.show("ll1")  # rejected: not Cyrillic
    s.run(1)
    assert s.out == []
    s.show("Кто здесь?")
    s.run(1)
    assert [u.text for u in s.out] == ["Кто здесь?"]


def test_latency_marks() -> None:
    s = Screen()
    s.show("Поехали!")
    t_appeared = s.clock.now()
    s.tick()  # the first frame with text is seen here
    s.run(1)
    (u,) = s.out
    assert u.t_change == pytest.approx(t_appeared + FRAME_S)
    assert u.t_detected >= u.t_change
