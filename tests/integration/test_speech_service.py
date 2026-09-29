"""SpeechService on fakes: scheduler + preparer + TTS + cache + player, driven by step()."""

from __future__ import annotations

import pytest

from subvoicer.config.resources import letter_names, translit_table
from subvoicer.core.events import SpeechDropped, SpeechFinished, SpeechStarted
from subvoicer.core.models import Priority, SpeechRequest
from subvoicer.pipeline.speech.cache import SpeechCache
from subvoicer.pipeline.speech.scheduler import SpeechScheduler
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.preparer import TextPreparer
from subvoicer.services.speech_service import SpeechService
from tests.fakes.audio import FakeOutput, FakeTts
from tests.fakes.capture import RecordingBus
from tests.fakes.clock import FakeClock
from tests.fakes.stress import FirstVowelStress


class Rig:
    def __init__(self) -> None:
        self.clock = FakeClock(100.0)
        self.tts = FakeTts()
        self.out = FakeOutput()
        self.bus = RecordingBus()
        self.service = SpeechService(
            SpeechScheduler(),
            TextPreparer(FirstVowelStress(), LatinReader(letter_names(), translit_table())),
            self.tts,
            self.out,
            SpeechCache(10),
            self.clock,
            self.bus,
        )

    def step(self, seconds: float = 0.01) -> None:
        self.clock.advance(seconds)
        self.service.step()


def request(
    id_: int, text: str = "Привет.", *, continuation: bool = False, channel: str = "z"
) -> SpeechRequest:
    return SpeechRequest(id_, text, "v1", Priority.HIGH, continuation=continuation, channel=channel)


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_line_is_prepared_synthesized_and_played(rig: Rig) -> None:
    rig.service.submit(request(1, "Сейчас 3 часа."))
    rig.step()
    assert rig.out.log == [("start", 1)]
    assert rig.tts.calls == [("С+ейчас тр+и ч+аса.", "v1", 1.0)]
    assert 1 in rig.out.done_feeding
    rig.out.sound_starts(1, 100.5)
    rig.step()
    (started,) = rig.bus.of(SpeechStarted)
    assert started.t_play == 100.5  # type: ignore[attr-defined]


def test_long_line_streams_piece_by_piece(rig: Rig) -> None:
    rig.service.submit(request(1, "Раз. Два. Три. Четыре."))
    rig.step()  # the first piece starts, the second is synthesized while it plays
    assert rig.out.fed[1] == 2 and 1 not in rig.out.done_feeding
    for _ in range(2):
        rig.step()
    assert rig.out.fed[1] == 4 and 1 in rig.out.done_feeding


def test_new_line_cuts_old_and_the_continuation_waits(rig: Rig) -> None:
    rig.service.submit(request(1, "Первая.", channel="a"))
    rig.step()
    rig.service.submit(request(2, "Вторая.", channel="b"))
    rig.step()
    assert rig.out.log == [("start", 1), ("stop", 1), ("start", 2)]
    rig.out.line_ends(1, completed=False)  # the fade of line 1 ends: nothing changes
    rig.service.submit(request(3, "хвост", continuation=True, channel="b"))
    rig.step()
    assert rig.out.log[-1] == ("start", 2)  # the continuation does not interrupt
    rig.out.line_ends(2)
    rig.step()
    assert rig.out.log[-1] == ("start", 3)
    finished = rig.bus.of(SpeechFinished)
    assert [(e.request.id, e.completed) for e in finished] == [(1, False), (2, True)]  # type: ignore[attr-defined]


def test_repeat_uses_the_cache(rig: Rig) -> None:
    rig.service.submit(request(1, "Повтори меня."))
    rig.step()
    rig.out.line_ends(1)
    rig.step()
    rig.service.repeat()
    rig.step()
    assert len(rig.tts.calls) == 1  # synthesized once
    assert rig.out.log[-1][0] == "start"


def test_pause_toggle(rig: Rig) -> None:
    rig.service.submit(request(1))
    rig.step()
    rig.service.toggle_pause()
    rig.step()
    assert rig.out.log[-1] == ("stop", 1)
    rig.service.submit(request(2))
    rig.step()
    assert [e.request.id for e in rig.bus.of(SpeechDropped)] == [2]  # type: ignore[attr-defined]
    rig.service.toggle_pause()
    rig.service.submit(request(3))
    rig.step()
    assert rig.out.log[-1] == ("start", 3)


def test_skip(rig: Rig) -> None:
    rig.service.submit(request(1))
    rig.step()
    rig.service.skip()
    rig.step()
    assert rig.out.log[-1] == ("stop", 1)


def test_text_that_prepares_to_nothing_finishes_at_once(rig: Rig) -> None:
    rig.service.submit(request(1, "…"))
    rig.step()
    rig.service.submit(request(2, "Дальше."))
    rig.step()
    assert rig.out.log == [("start", 2)]
