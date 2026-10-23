"""Zone source end to end on fakes: the stage 2 criteria that do not need a real game."""

from __future__ import annotations

import numpy as np
import pytest

from subvoicer.config.resources import short_words, speaker_stoplist
from subvoicer.core.models import Frame, OcrLine, OcrResult, Rect, Utterance, UtteranceKind
from subvoicer.pipeline.dedup import Deduplicator
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner, OcrSettings
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.text.skip import SkipList
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
    def __init__(
        self, speaker: SpeakerMode = SpeakerMode.NONE, skip: SkipList | None = None
    ) -> None:
        self.clock = FakeClock(100.0)
        self.ocr = ScriptedOcr()
        zone = ZoneSpec(
            id="dialog",
            rect=Rect(0.0, 0.0, 1.0, 1.0),
            mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
            speaker=SpeakerResolver(speaker, speaker_stoplist()),
            skip=skip,
        )
        self.source = ZoneOcrSource(
            [zone], OcrRunner(self.ocr), JunkFilter(short_words()), Deduplicator(), self.clock
        )
        self.frame: Frame | None = None
        self.out: list[Utterance] = []

    def show(self, text: str, *, typed: int | None = None, background: int = 20) -> None:
        """Draw ``text`` (``typed`` characters of it) as a white glyph per character: its
        height and offset depend on the character, so other text puts pixels elsewhere (as real
        letters do) and does not look like the old text fading (TZ 1.27)."""
        image = np.full((H, W, 4), background, dtype=np.uint8)
        visible = text if typed is None else text[:typed]
        for i, char in enumerate(visible):
            x = 5 + (i % 60) * 6
            y = 10 + (i // 60) * 30 + ord(char) % 5
            image[y : y + 12 + ord(char) % 7, x : x + 4, :3] = 255
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


def test_a_short_line_read_in_latin_twins_is_voiced() -> None:
    """«Нет» read as «HeT» was dropped by the junk filter as not Cyrillic."""
    s = Screen()
    s.show("HeT")
    s.run(2)
    assert [u.text for u in s.out] == ["Нет"]


def test_hints_of_the_skip_list_are_not_voiced() -> None:
    """TZ 1.33: «Не озвучивать» — an on-screen hint in a dark scene, the next line is voiced."""
    s = Screen(SpeakerMode.PREFIX, SkipList(["[Е] Взять"]))
    s.show("[E] Взять патроны")
    s.run(2)
    s.show("Джуди: Взять их всех живыми.")
    s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [("Джуди", "Взять их всех живыми.")]


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


class ByOrderOcr:
    """The zone's text is read first, then its name (a small name cut-out is enlarged, so
    its width tells nothing); records what it was shown."""

    def __init__(
        self, name: str, text: str, box: tuple[int, int, int, int] = (0, 0, 10, 10)
    ) -> None:
        self.name, self.text, self.box = name, text, box  # box: where the text line is
        self.images: list[np.ndarray] = []

    def recognize(self, image: np.ndarray) -> OcrResult:
        self.images.append(image)
        said = self.text if len(self.images) % 2 else self.name
        return OcrResult((OcrLine(said, 0.97, self.box),))


def run_named(zone: ZoneSpec, image: np.ndarray, ocr: ByOrderOcr) -> list[Utterance]:
    clock = FakeClock(100.0)
    runner = OcrRunner(ocr, OcrSettings(upscale=1.0))  # the fake's boxes are crop pixels
    source = ZoneOcrSource([zone], runner, JunkFilter(short_words()), Deduplicator(), clock)
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
    out = run_named(zone, image, ByOrderOcr("Джуди", "Ты вовремя."))
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
    ocr = ByOrderOcr("Джуди", "Ты вовремя.")
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
    ocr = ByOrderOcr("Панам", "Поехали отсюда.", (100, 30, 780, 50))
    out = run_named(zone, image, ocr)
    assert [(u.speaker, u.text) for u in out] == [("Панам", "Поехали отсюда.")]
    text_image, name_image = ocr.images
    red_pixels = (text_image[..., 2] > 200) & (text_image[..., 1] < 50)
    assert not red_pixels.any()  # the name is painted out of the text input
    assert name_image.shape[1] < text_image.shape[1] / 2  # the name part, not the zone


def test_a_name_coloured_label_above_the_subtitle_is_not_the_name() -> None:
    """Cyberpunk: the red «ОМЕГА-БЛОКАТОРЫ» above; specks of the text colour by it threw off
    the text mask's first line, and the whole zone's red was read as the name."""
    image = np.full((H, 800, 4), 20, dtype=np.uint8)
    image[5:20, 300:500, :3] = (0, 0, 255)  # a red HUD label, far above the line
    image[8:12, 700:710, :3] = 255  # a speck of the text colour up there
    image[50:70, 10:80, :3] = (0, 0, 255)  # the name
    image[50:70, 100:780, :3] = 255  # the text
    red = (((0, 100, 100), (10, 255, 255)),)
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.COLOR, speaker_stoplist()),
        name=NameSource(MaskSpec(MaskPreset.COLOR, colors_hsv=red)),
    )
    ocr = ByOrderOcr("Панам", "Поехали отсюда.", (100, 50, 780, 70))
    out = run_named(zone, image, ocr)
    assert [(u.speaker, u.text) for u in out] == [("Панам", "Поехали отсюда.")]
    _text_image, name_image = ocr.images
    assert name_image.shape[1] < 400  # the name only (~240 enlarged), with the label > 600


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


def test_blink_of_the_same_subtitle_is_not_a_new_line() -> None:
    s = Screen()
    s.show("Я бы чего-нибудь заточил.")
    s.run(1)
    s.show("")
    s.tick()  # one empty frame, ~100 ms
    s.show("Я бы чего-нибудь заточил!")  # OCR reads it a bit differently
    s.run(1)
    assert [u.text for u in s.out] == ["Я бы чего-нибудь заточил."]


def test_short_gap_before_a_different_line_is_still_a_new_line() -> None:
    s = Screen()
    s.show("Без обид.")
    s.run(1)
    s.show("")
    s.tick()
    s.show("Ну а чё, как по мне, это хорошее начало.")
    s.run(1)
    assert [u.text for u in s.out] == ["Без обид.", "Ну а чё, как по мне, это хорошее начало."]


STACK_A = "Хелльман: Простите, я…"
STACK_B = "Ёринобу: Располагайся пока, я закончу через минуту."
STACK_C = "Ти-Баг: Надо послушать их разговор. Там может быть что-то важное."


def test_stacked_subtitles_voice_each_line_once() -> None:
    """TZ 1.22, Cyberpunk: a new line appears below, the old one moves up and goes away."""
    s = Screen(SpeakerMode.PREFIX)
    for text in (STACK_A, f"{STACK_A} {STACK_B}", f"{STACK_B} {STACK_C}", STACK_B):
        s.show(text)
        s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Хелльман", "Простите, я…"),
        ("Ёринобу", "Располагайся пока, я закончу через минуту."),
        ("Ти-Баг", "Надо послушать их разговор. Там может быть что-то важное."),
    ]
    assert all(u.kind is UtteranceKind.NEW for u in s.out)  # names start new lines (TZ 1.22)


def test_stacked_names_in_brackets_voice_each_line_once() -> None:
    """TZ 1.33, Atomic Heart: «[ Имя ]» over each line; a new line comes below, the old one
    moves up."""
    first = "[ ПРЕДСТАВИТЕЛЬ ПРЕДПРИЯТИЯ ] Приветствую на предприятии."
    second = "[ П-3 ] Да-да, здравствуйте."
    s = Screen(SpeakerMode.BRACKETS)
    for text in (first, f"{first} {second}", second):
        s.show(text)
        s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Представитель предприятия", "Приветствую на предприятии."),
        ("П-3", "Да-да, здравствуйте."),
    ]


def test_cut_top_and_revealed_line_are_not_voiced_again() -> None:
    """The long upper line is cut by the zone edge while another is below, then seen whole."""
    long_line = (
        "Ёринобу: Сабуро безумный деспот, который потерял связь с реальностью. "
        "Он живёт в мире, которого уже нет!"
    )
    s = Screen(SpeakerMode.PREFIX)
    for text in (
        long_line,
        "связь с реальностью. Он живёт в мире, которого уже нет! Ти-Баг: Нет. Только цену.",
        long_line,
    ):
        s.show(text)
        s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Ёринобу", long_line.removeprefix("Ёринобу: ")),
        ("Ти-Баг", "Нет. Только цену."),
    ]


def test_a_line_that_came_back_after_another_one_is_voiced_again() -> None:
    s = Screen(SpeakerMode.PREFIX)
    for text in ("Ви: Пошли отсюда.", "Джеки: Не сейчас, чумба.", "Ви: Пошли отсюда."):
        s.show(text)
        s.run(2)
    assert [u.text for u in s.out] == ["Пошли отсюда.", "Не сейчас, чумба.", "Пошли отсюда."]


def test_a_bare_name_before_its_text_is_not_voiced() -> None:
    """TZ 1.22: the next line fades in, its red name is readable before the text."""
    s = Screen(SpeakerMode.PREFIX)
    first = "Ёринобу: Ничего. Скоро пройдёт."
    for text in (first, f"{first} Эвелин:", f"{first} Эвелин: Ага, я этим займусь."):
        s.show(text)
        s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Ёринобу", "Ничего. Скоро пройдёт."),
        ("Эвелин", "Ага, я этим займусь."),
    ]


def test_the_end_of_the_line_cut_by_length_is_not_voiced() -> None:
    """TZ 1.22: OCR glues words in the second reading, the "new" part starts a few letters
    too early ("ло! Ви: …") — only Ви's line is new."""
    s = Screen(SpeakerMode.PREFIX)
    s.show("Ёринобу: потерял связь с реальностью. Может, никогда и не было!")
    s.run(2)
    s.show("Ёринобу: потерялсвязь среальностью. Может, никогда и не было! Ви: Неплохо.")
    s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Ёринобу", "потерял связь с реальностью. Может, никогда и не было!"),
        ("Ви", "Неплохо."),
    ]


def test_the_end_of_the_line_is_not_voiced_when_ocr_spaces_it_out() -> None:
    """The other way round: more spaces in the second reading cut "ыло!" off the old line."""
    s = Screen(SpeakerMode.PREFIX)
    s.show("Ёринобу: потерялсвязь среальностью. Может, никогда и не было!")
    s.run(2)
    s.show("Ёринобу: потерял связь с реальностью. Может, никогда и не было! Ви: Неплохо.")
    s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Ёринобу", "потерялсвязь среальностью. Может, никогда и не было!"),
        ("Ви", "Неплохо."),
    ]


def test_a_line_without_a_name_above_a_voiced_one() -> None:
    """TZ 1.22: a line without a name appears above Judy's; only it is new."""
    s = Screen(SpeakerMode.PREFIX)
    judy = "Джуди: Я вижу ходячие говорящие трупы."
    for text in (judy, f"Расслабься, все под контролем. {judy}"):
        s.show(text)
        s.run(2)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Джуди", "Я вижу ходячие говорящие трупы."),
        (None, "Расслабься, все под контролем."),
    ]


@pytest.mark.parametrize(
    "second",
    ["Джеки: Не сейчас, чумба.", "Джеки: Да ну, чумба!!", "Джеки: Ну."],
    ids=["longer", "same length", "shorter"],
)
def test_a_line_replaced_at_once_is_read_whatever_its_length(second: str) -> None:
    """TZ 1.27: the fade rule must not swallow another line put in place of the old one
    without an empty frame between them — it adds its own letters."""
    s = Screen(SpeakerMode.PREFIX)
    for text in ("Ви: Пошли уже, чумба!", second):
        s.show(text)
        s.run(2)
    assert [u.speaker for u in s.out] == ["Ви", "Джеки"]


def test_second_half_of_a_split_line_continues_it() -> None:
    """TZ 1.28: the game split one line in two subtitles (Alan Wake): the second half is a
    continuation — the same voice, right after the first, never cutting it."""
    s = Screen(SpeakerMode.PREFIX)
    s.show("Алан: А теперь это происходило на самом деле, и я не")
    s.run(1)
    s.show("мог выдавить из себя ни строчки.")
    s.run(1)
    assert [(u.kind, u.speaker, u.text) for u in s.out] == [
        (UtteranceKind.NEW, "Алан", "А теперь это происходило на самом деле, и я не"),
        (UtteranceKind.CONTINUATION, "Алан", "мог выдавить из себя ни строчки."),
    ]


def test_a_new_sentence_or_a_late_line_is_not_a_continuation() -> None:
    s = Screen()
    s.show("Мост перекрыт.")
    s.run(1)
    s.show("едем в объезд через доки")  # the first one ended with a full stop
    s.run(1)
    s.show("Встретимся у моста, если")
    s.run(1)
    s.run(20)
    s.show("потом поговорим об этом подробнее.")  # too late after the first half
    s.run(1)
    assert [u.kind for u in s.out] == [UtteranceKind.NEW] * 4


def test_a_colour_zone_reads_only_lines_of_its_colour() -> None:
    """TZ 1.35: a yellow «15M» under a cyan Cyberpunk subtitle is not read."""
    cyan = ((80, 90, 170), (100, 255, 255))
    image = np.full((120, 400, 4), 25, dtype=np.uint8)
    for x in range(60, 360, 8):
        image[20:40, x : x + 4, :3] = (230, 210, 40)
    for x in range(180, 220, 8):
        image[80:95, x : x + 4, :3] = (40, 210, 240)

    class TwoLines:
        def recognize(self, _image: np.ndarray) -> OcrResult:
            return OcrResult(
                (
                    OcrLine("Видишь, какая-то польза от тебя.", 0.95, (55, 15, 365, 45)),
                    OcrLine("15M", 0.9, (175, 75, 225, 100)),
                )
            )

    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.COLOR, colors_hsv=(cyan,)),
        speaker=SpeakerResolver(SpeakerMode.NONE, speaker_stoplist()),
    )
    clock = FakeClock(100.0)
    source = ZoneOcrSource(
        [zone],
        OcrRunner(TwoLines(), OcrSettings(upscale=1.0)),
        JunkFilter(short_words()),
        Deduplicator(),
        clock,
    )
    out: list[Utterance] = []
    for _ in range(20):
        clock.advance(FRAME_S)
        out += source.process(Frame(image, clock.now()))
    assert [u.text for u in out] == ["Видишь, какая-то польза от тебя."]


# ---------------------------------------------------------------- TZ 1.36: the name colour

RED = (((0, 100, 100), (10, 255, 255)),)


class InTurn:
    """Gives the next of its results on every call (text, name, text, name…)."""

    def __init__(self, *results: tuple[OcrLine, ...]) -> None:
        self.results = results
        self.calls = 0

    def recognize(self, _image: np.ndarray) -> OcrResult:
        result = self.results[self.calls % len(self.results)]
        self.calls += 1
        return OcrResult(result)


def run_zone(zone: ZoneSpec, image: np.ndarray, ocr: object) -> list[Utterance]:
    clock = FakeClock(100.0)
    runner = OcrRunner(ocr, OcrSettings(upscale=1.0))  # type: ignore[arg-type]
    source = ZoneOcrSource([zone], runner, JunkFilter(short_words()), Deduplicator(), clock)
    out: list[Utterance] = []
    for _ in range(10):
        clock.advance(FRAME_S)
        out += source.process(Frame(image, clock.now()))
    return out


def test_a_name_of_its_colour_on_the_row_above() -> None:
    """«Над текстом»: the red name on its own row over the line."""
    image = np.full((H, 800, 4), 20, dtype=np.uint8)
    image[10:28, 10:80, :3] = (0, 0, 255)  # the name, a row above
    image[40:60, 10:780, :3] = 255  # the text
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.COLOR, speaker_stoplist()),
        name=NameSource(MaskSpec(MaskPreset.COLOR, colors_hsv=RED), place="above"),
    )
    ocr = ByOrderOcr("Панам", "Поехали отсюда.", (10, 40, 780, 60))
    assert [(u.speaker, u.text) for u in run_zone(zone, image, ocr)] == [
        ("Панам", "Поехали отсюда.")
    ]


def test_brackets_of_another_colour_are_text_not_a_name() -> None:
    """«В скобках» with the name colour: a yellow «[ Открыть ]» hint is not a speaker."""
    text = "[ Открыть ] Взять. [ Нора ] Ну и ну."
    image = np.full((H, 800, 4), 20, dtype=np.uint8)
    image[40:60, 0 : len(text) * 10, :3] = 255
    first, second = text.index("[ Открыть ]"), text.index("[ Нора ]")
    image[40:60, first * 10 : (first + 11) * 10, :3] = (40, 210, 240)  # yellow
    image[40:60, second * 10 : (second + 8) * 10, :3] = (0, 0, 255)  # red, the name colour
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.BRACKETS, speaker_stoplist()),
        name_colours=RED,
    )
    ocr = InTurn((OcrLine(text, 0.95, (0, 40, len(text) * 10, 60)),))
    assert [(u.speaker, u.text) for u in run_zone(zone, image, ocr)] == [
        (None, "Открыть Взять."),
        ("Нора", "Ну и ну."),
    ]


def test_a_name_row_of_the_name_colour_stays_in_a_colour_zone() -> None:
    """The text is cyan by colour, «[ П-3 ]» over it is red: the row is the name, not junk of
    another colour — a red label without brackets still goes."""
    cyan = ((80, 90, 170), (100, 255, 255))
    image = np.full((H, 800, 4), 25, dtype=np.uint8)
    image[5:15, 300:500, :3] = (0, 0, 255)  # a red HUD label
    image[22:36, 10:100, :3] = (0, 0, 255)  # «[ П-3 ]»
    for x in range(10, 780, 8):
        image[45:65, x : x + 4, :3] = (230, 210, 40)  # the cyan line
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.COLOR, colors_hsv=(cyan,)),
        speaker=SpeakerResolver(SpeakerMode.BRACKETS, speaker_stoplist()),
        name_colours=RED,
    )
    ocr = InTurn(
        (
            OcrLine("ОМЕГА-БЛОКАТОРЫ", 0.95, (300, 3, 500, 17)),
            OcrLine("[ П-3 ]", 0.95, (10, 20, 100, 38)),
            OcrLine("Да-да, здравствуйте.", 0.95, (10, 45, 780, 65)),
        )
    )
    assert [(u.speaker, u.text) for u in run_zone(zone, image, ocr)] == [
        ("П-3", "Да-да, здравствуйте.")
    ]


def test_a_name_zone_with_the_name_colour_reads_only_it() -> None:
    """«Зона» with the name colour: a yellow «15M» in the name zone is not part of the name."""
    image = np.full((H, W, 4), 20, dtype=np.uint8)
    image[4:16, 10:60, :3] = (0, 0, 255)  # the name, red
    image[4:16, 80:110, :3] = (40, 210, 240)  # something yellow in the name zone
    image[40:60, 10:390, :3] = 255  # the text
    zone = ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.4, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
        speaker=SpeakerResolver(SpeakerMode.ZONE, speaker_stoplist()),
        name=NameSource(MaskSpec(MaskPreset.COLOR, colors_hsv=RED), Rect(0.0, 0.0, 0.3, 0.25)),
    )
    ocr = InTurn(
        (OcrLine("Ты вовремя.", 0.97, (10, 0, 390, 20)),),
        (
            OcrLine("Джуди", 0.97, (30, 12, 180, 48)),
            OcrLine("15M", 0.97, (240, 12, 330, 48)),
        ),
    )
    assert [(u.speaker, u.text) for u in run_zone(zone, image, ocr)] == [("Джуди", "Ты вовремя.")]
