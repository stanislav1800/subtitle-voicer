"""A «Субтитры (авто)» zone end to end on fakes (TZ 4.2.2): plates are found in the search
area, each plate is a line of its own, a line still on screen is not voiced again."""

from __future__ import annotations

import numpy as np

from subvoicer.app.game_runner import plate_stabilization
from subvoicer.config.resources import short_words, speaker_stoplist
from subvoicer.core.models import Frame, OcrLine, OcrResult, Rect, Utterance, UtteranceKind
from subvoicer.pipeline.dedup import Deduplicator
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec
from subvoicer.pipeline.vision.stability import StabilizationSettings
from subvoicer.sources.zone_ocr import PlateSearch, ZoneOcrSource, ZoneSpec, read_once
from tests.fakes.clock import FakeClock

H, W = 360, 1280
FRAME_S = 0.1
Box = tuple[int, int, int, int]


class Screen:
    """A noisy scene with plates on it; the detector reports the plates' text lines, the
    OCR tells the plates apart by their width (the crop it gets is one plate)."""

    def __init__(self, locate_ms: int = 300, cooldown_min: float = 0) -> None:
        self.clock = FakeClock(100.0)
        self.plates: dict[Box, str] = {}
        self.hud: Box | None = None
        self.ocr_calls = 0
        self.detector_calls = 0
        self.misses = 0  # searches the detector sees nothing
        self.noise = 0  # bright specks on every plate, new each frame (a shimmering edge)
        zone = ZoneSpec(
            id="subtitles_auto",
            rect=Rect(0.0, 0.0, 1.0, 1.0),
            mask=MaskSpec(MaskPreset.BRIGHT_ON_DARK),
            speaker=SpeakerResolver(SpeakerMode.PREFIX, speaker_stoplist()),
            plates=PlateSearch(self, locate_ms=locate_ms),
            stabilization=plate_stabilization(StabilizationSettings()),
            repeat_cooldown_min=cooldown_min,
        )
        self.zone = zone
        self.source = ZoneOcrSource(
            [zone], OcrRunner(self), JunkFilter(short_words()), Deduplicator(), self.clock
        )
        self.out: list[Utterance] = []
        self.rng = np.random.default_rng(3)

    # the text detector and the OCR engine of the zone
    def detect_lines(self, image: np.ndarray) -> list[Box]:
        self.detector_calls += 1
        if self.misses:
            self.misses -= 1
            return []
        lines = [self._line(box) for box in self.plates]
        return lines + ([self.hud] if self.hud else [])

    def recognize(self, image: np.ndarray) -> OcrResult:
        self.ocr_calls += 1
        aspect = image.shape[1] / image.shape[0]
        box, text = min(
            self.plates.items(),
            key=lambda item: abs((item[0][2] - item[0][0]) / (item[0][3] - item[0][1]) - aspect),
        )
        share = aspect / ((box[2] - box[0]) / (box[3] - box[1]))
        if share < 0.8:  # a crop narrower than the plate: the middle of its text
            cut = round(len(text) * (1 - share) / 2)
            text = text[cut : len(text) - cut]
        return OcrResult((OcrLine(text, 0.97, (0, 0, 10, 10)),))

    @staticmethod
    def _line(box: Box) -> Box:
        x1, y1, x2, y2 = box
        pad = (y2 - y1) // 4
        return (x1 + pad, y1 + pad, x2 - pad, y2 - pad)

    def frame(self) -> Frame:
        image = self.rng.integers(60, 140, (H, W, 3), dtype=np.uint8)
        for box in self.plates:
            x1, y1, x2, y2 = box
            image[y1:y2, x1:x2] = 12
            lx1, ly1, lx2, ly2 = self._line(box)
            for x in range(lx1, lx2 - 3, 9):
                image[ly1:ly2, x : x + 3] = 235
            for _ in range(self.noise):
                y, x = self.rng.integers(ly1, ly2), self.rng.integers(lx1, lx2)
                image[y, x] = 235
        if self.hud:
            x1, y1, x2, y2 = self.hud
            for x in range(x1, x2 - 3, 9):
                image[y1:y2, x : x + 3] = 235
        return Frame(image, self.clock.now())

    def run(self, seconds: float) -> None:
        for _ in range(round(seconds / FRAME_S)):
            self.clock.advance(FRAME_S)
            self.out += self.source.process(self.frame())


AD = "Пока вы у нас, рекомендую скандинавскую ходьбу."
HELLO = "Алло? Алло!"


def test_a_plate_is_voiced_once() -> None:
    s = Screen()
    s.plates = {(340, 262, 940, 322): AD}
    s.run(3)
    assert [u.text for u in s.out] == [AD]
    assert s.detector_calls <= 3 / 0.3 + 1  # plates looked for every 300 ms, not every frame


def test_a_new_plate_above_a_line_still_on_screen_is_voiced_alone() -> None:
    """Alan Wake: a short line appears over the long one still showing — only it sounds
    (it used to be lost, glued to the long one)."""
    s = Screen()
    s.plates = {(340, 262, 940, 322): AD}
    s.run(2)
    s.plates = {(540, 200, 740, 250): HELLO, (340, 262, 940, 322): AD}
    s.run(2)
    assert [(u.kind, u.text) for u in s.out] == [
        (UtteranceKind.NEW, AD),
        (UtteranceKind.NEW, HELLO),
    ]


def test_text_without_a_plate_is_not_read_and_a_gone_plate_empties_the_zone() -> None:
    s = Screen()
    s.hud = (40, 40, 400, 80)  # HUD letters right on the scene
    s.run(2)
    assert s.out == [] and s.ocr_calls == 0
    s.plates = {(340, 262, 940, 322): AD}
    s.run(2)
    s.plates = {}
    s.run(1)
    s.plates = {(340, 262, 940, 322): AD}  # the same line said again after a pause
    s.run(2)
    assert [u.text for u in s.out] == [AD, AD]


def test_a_plate_missed_by_one_search_is_not_voiced_again() -> None:
    """Alan Wake: the detector lost a plate for one search; it used to come back after
    ~300 ms — longer than the flicker rule — and «Нет, так не пойдет.» sounded twice."""
    s = Screen()
    s.plates = {(340, 262, 940, 322): AD}
    s.run(1.5)
    for _ in range(3):
        s.misses = 1
        s.run(1.5)
    assert [u.text for u in s.out] == [AD]


def test_a_short_line_on_a_shimmering_plate_is_read_before_it_goes() -> None:
    """Alan Wake: «Нет!» after a long line — a small plate, ~1 s on screen; specks at the
    letters kept the zone «changing» and the line went away unread."""
    s = Screen()
    s.noise = 60
    s.plates = {(560, 262, 720, 322): "Нет!"}
    s.run(1.0)
    s.plates = {}
    s.run(1)
    assert [u.text for u in s.out] == ["Нет!"]


def test_read_once_reads_plates_only() -> None:
    s = Screen()
    s.plates = {(540, 200, 740, 250): HELLO, (340, 262, 940, 322): AD}
    s.hud = (40, 40, 400, 80)
    reading = read_once(s.zone, s.frame().image, OcrRunner(s), separator="\n")
    assert reading.text == f"{HELLO}\n{AD}"
    assert len(reading.plates) == 2


def test_the_old_plate_going_away_voices_nothing_again() -> None:
    s = Screen()
    s.plates = {(340, 262, 940, 322): AD}
    s.run(2)
    s.plates = {(540, 200, 740, 250): HELLO, (340, 262, 940, 322): AD}
    s.run(2)
    s.plates = {(540, 200, 740, 250): HELLO}  # the long line ends, the short one stays
    s.run(2)
    assert [u.text for u in s.out] == [AD, HELLO]


def test_plates_for_the_overlay_are_in_frame_fractions() -> None:
    s = Screen()
    s.plates = {(340, 262, 940, 322): AD}
    s.run(1)
    ((x1, y1, x2, y2),) = s.source.plates()["subtitles_auto"]
    assert abs(x1 - 340 / W) < 0.01 and abs(y2 - 322 / H) < 0.01
    assert abs(x2 - 940 / W) < 0.01 and abs(y1 - 262 / H) < 0.01


def test_a_hint_plate_of_the_skip_list_is_dropped_the_line_beside_it_is_voiced() -> None:
    """TZ 1.33: «Не озвучивать» works plate by plate."""
    from dataclasses import replace

    from subvoicer.pipeline.text.skip import SkipList

    s = Screen()
    s.zone = replace(s.zone, skip=SkipList(["Влезть"]))
    s.source = ZoneOcrSource(
        [s.zone], OcrRunner(s), JunkFilter(short_words()), Deduplicator(), s.clock
    )
    s.plates = {(560, 40, 720, 100): "Влезть", (340, 262, 940, 322): AD}
    s.run(3)
    assert [u.text for u in s.out] == [AD]


def test_a_name_plate_in_brackets_belongs_to_the_plate_below() -> None:
    """TZ 1.33: «[ П-3 ]» over its line is found as a plate of its own."""
    from dataclasses import replace

    s = Screen()
    s.zone = replace(s.zone, speaker=SpeakerResolver(SpeakerMode.BRACKETS, speaker_stoplist()))
    s.source = ZoneOcrSource(
        [s.zone], OcrRunner(s), JunkFilter(short_words()), Deduplicator(), s.clock
    )
    s.plates = {(560, 200, 660, 250): "[ П-3 ]", (340, 262, 940, 322): AD}
    s.run(3)
    assert [(u.speaker, u.text) for u in s.out] == [("П-3", AD)]


def test_a_long_line_in_place_of_a_short_one_is_read_whole() -> None:
    """RDR2 (TZ 1.41): a long line came in place of a short one; the short one's plate was
    still «there» under it, and its middle was voiced — «оровые молодые женщины просят».
    Plates are looked for less often than the text holds still, as in the game's frames."""
    s = Screen(locate_ms=900)
    s.plates = {(540, 262, 740, 322): HELLO}
    s.run(2.3)  # the line changes right after a search: the next one is after the read
    s.plates = {(140, 262, 1140, 322): AD}
    s.run(2)
    assert [u.text for u in s.out] == [HELLO, AD]


def test_a_plate_fading_in_is_read_once_it_is_whole() -> None:
    """RDR2: the detector found a part of a plate fading in; it was read at once."""
    s = Screen(locate_ms=900)
    s.plates = {(390, 262, 890, 322): AD}  # what the detector sees first
    s.run(0.2)
    s.plates = {(140, 262, 1140, 322): AD}
    s.run(2)
    assert [u.text for u in s.out] == [AD]


def test_a_line_voiced_a_moment_ago_is_not_voiced_again_under_another() -> None:
    """RDR2 (TZ 1.41): «Билл: …» went, «Незнакомец: …» came, then «Билл: …» again under it —
    the reading was the stranger's plate grown by Bill's line, and Bill's line sounded twice."""
    bill, stranger = "Билл: Морган, иди сюда и дерись!", "Незнакомец: Черт побери, что происходит?"
    s = Screen(cooldown_min=2)
    s.plates = {(390, 262, 890, 322): bill}
    s.run(1.5)
    s.plates = {}
    s.run(2)
    s.plates = {(240, 262, 1040, 322): stranger}
    s.run(1.5)
    s.plates = {(140, 262, 1140, 322): f"{stranger} {bill}"}  # one plate, two lines
    s.run(1.5)
    assert [(u.speaker, u.text) for u in s.out] == [
        ("Билл", "Морган, иди сюда и дерись!"),
        ("Незнакомец", "Черт побери, что происходит?"),
    ]
