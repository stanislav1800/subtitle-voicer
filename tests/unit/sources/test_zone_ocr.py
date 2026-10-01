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
from subvoicer.sources.zone_ocr import NameSource, ZoneOcrSource, ZoneSpec, drop_last_word
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


def test_typewriter_longer_than_max_wait_is_still_one_line() -> None:
    s = Screen()
    line = "Слушай внимательно, я не буду повторять дважды, потому что времени у нас совсем нет."
    for typed in range(1, len(line) + 1, 2):  # 2 characters per frame: ~4.3 s of typing
        s.show(line, typed=typed)
        s.tick()
    s.show(line)
    s.run(1)
    assert [(u.kind, u.text) for u in s.out] == [(UtteranceKind.NEW, line)]
    assert s.ocr.calls == 1


def test_very_long_typing_is_forced_and_continued() -> None:
    s = Screen()
    line = "Раз два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать."
    for typed in range(1, len(line) + 1):  # 1 character per frame: ~8 s of typing
        s.show(line, typed=typed)
        s.tick()
    s.show(line)
    s.run(1)
    assert s.out[0].kind is UtteranceKind.NEW
    assert [u.kind for u in s.out[1:]] == [UtteranceKind.CONTINUATION]
    assert " ".join(u.text for u in s.out) == line  # cut at a word boundary


def test_short_last_word_after_forced_read_is_not_lost() -> None:
    """Forced at 5 s mid-word: the cut word is dropped and comes back whole in the tail,
    which is then long enough to count as a continuation (> 3 characters)."""
    s = Screen()
    line = "Охрана на входе сменится через двадцать минут, у нас будет окно, не опаздывай."
    for typed in range(1, len(line) + 1):  # ~5.1 s of typing: the 5 s cap hits at the end
        s.show(line, typed=typed)
        s.tick(0.065)
    s.show(line)
    s.run(1)
    assert " ".join(u.text for u in s.out) == line
    assert s.out[-1].text.endswith("опаздывай.")


def test_drop_last_word() -> None:
    assert drop_last_word("Охрана на входе сме") == "Охрана на входе"
    assert drop_last_word("Охрана ") == "Охрана"
    assert drop_last_word("Охрана") == "Охрана"


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


# ---------------------------------------------------------------- speaker zone and colour (4.5)


class ByWidthOcr:
    """Narrow images are the name, wide ones the text; records what it was shown."""

    def __init__(self, name: str, text: str) -> None:
        self.name, self.text = name, text
        self.images: list[np.ndarray] = []

    def recognize(self, image: np.ndarray) -> OcrResult:
        self.images.append(image)
        said = self.name if image.shape[1] < 500 else self.text
        return OcrResult((OcrLine(said, 0.97, (0, 0, 10, 10)),))


def run_named(zone: ZoneSpec, image: np.ndarray, ocr: ByWidthOcr) -> list[Utterance]:
    clock = FakeClock(100.0)
    source = ZoneOcrSource([zone], OcrRunner(ocr), JunkFilter(short_words()), Deduplicator(), clock)
    out: list[Utterance] = []
    for _ in range(10):
        clock.advance(FRAME_S)
        out += source.process(Frame(image, clock.now()))
    return out


def test_speaker_from_a_speaker_zone() -> None:
    image = np.full((H, W, 4), 20, dtype=np.uint8)
    image[4:16, 10:60, :3] = 255  # the name, top-left
    image[40:60, 10:390, :3] = 255  # the text
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.4, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.ZONE, speaker_stoplist()),
        name=NameSource(MaskSpec(MaskPreset.BRIGHT_ON_DARK), Rect(0.0, 0.0, 0.3, 0.25)),
    )
    out = run_named(zone, image, ByWidthOcr("Джуди", "Ты вовремя."))
    assert [(u.speaker, u.text) for u in out] == [("Джуди", "Ты вовремя.")]


def test_empty_speaker_zone_gives_no_name() -> None:
    image = np.full((H, W, 4), 20, dtype=np.uint8)
    image[40:60, 10:390, :3] = 255
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.4, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.ZONE, speaker_stoplist()),
        name=NameSource(MaskSpec(MaskPreset.BRIGHT_ON_DARK), Rect(0.0, 0.0, 0.3, 0.25)),
    )
    ocr = ByWidthOcr("Джуди", "Ты вовремя.")
    out = run_named(zone, image, ocr)
    assert [(u.speaker, u.text) for u in out] == [(None, "Ты вовремя.")]
    assert len(ocr.images) == 1  # the empty speaker zone is not recognised


def test_speaker_by_colour_inside_the_zone() -> None:
    image = np.full((H, 800, 4), 20, dtype=np.uint8)
    image[30:50, 10:80, :3] = (0, 0, 255)  # the name in red (BGR)
    image[30:50, 100:780, :3] = 255  # the text in white
    red = (((0, 100, 100), (10, 255, 255)),)
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.COLOR, speaker_stoplist()),
        name=NameSource(MaskSpec(MaskPreset.COLOR, colors_hsv=red)),
    )
    ocr = ByWidthOcr("Панам", "Поехали отсюда.")
    out = run_named(zone, image, ocr)
    assert [(u.speaker, u.text) for u in out] == [("Панам", "Поехали отсюда.")]
    text_image, name_image = ocr.images
    red_pixels = (text_image[..., 2] > 200) & (text_image[..., 1] < 50)
    assert not red_pixels.any()  # the name is painted out of the text input
    assert name_image.shape[1] < 200  # only the name part (78 px, upscaled x2) is read


# ---------------------------------------------------------------- TZ 1.17


def test_two_speakers_in_one_subtitle_are_two_lines() -> None:
    s = Screen(SpeakerMode.PREFIX)
    s.show("Ви: А ты ещё кто? Джеки: Вылезай.")
    s.run(1)
    assert [(u.kind, u.speaker, u.text) for u in s.out] == [
        (UtteranceKind.NEW, "Ви", "А ты ещё кто?"),
        (UtteranceKind.NEW, "Джеки", "Вылезай."),
    ]
    assert s.out[0].id != s.out[1].id


def test_continuation_with_another_speaker() -> None:
    s = Screen(SpeakerMode.PREFIX)
    s.show("Ви: Не понял, ты кто?")
    s.run(1)
    s.show("Ви: Не понял, ты кто? Я был первый. Джеки: Можем решить по-хорошему.")
    s.run(1)
    assert [(u.kind, u.speaker, u.text) for u in s.out] == [
        (UtteranceKind.NEW, "Ви", "Не понял, ты кто?"),
        (UtteranceKind.CONTINUATION, "Ви", "Я был первый."),
        (UtteranceKind.NEW, "Джеки", "Можем решить по-хорошему."),
    ]


class FallbackOcr:
    """The default input is unsure but has the name; the fallback is sure but lost it."""

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, image: np.ndarray) -> OcrResult:
        self.calls += 1
        if self.calls % 3 == 1:
            return OcrResult((OcrLine("Кирк: Мой человек на парковке.", 0.7, (0, 0, 10, 10)),))
        return OcrResult((OcrLine("Мой человек на парковке.", 0.95, (0, 0, 10, 10)),))


def test_name_survives_a_fallback_input() -> None:
    image = np.full((H, W, 4), 20, dtype=np.uint8)
    image[30:50, 10:390, :3] = 255
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.PREFIX, speaker_stoplist()),
    )
    clock = FakeClock(100.0)
    source = ZoneOcrSource(
        [zone], OcrRunner(FallbackOcr()), JunkFilter(short_words()), Deduplicator(), clock
    )
    out: list[Utterance] = []
    for _ in range(10):
        clock.advance(FRAME_S)
        out += source.process(Frame(image, clock.now()))
    assert [(u.speaker, u.text, u.ocr_input) for u in out] == [
        ("Кирк", "Мой человек на парковке.", "masked")
    ]
