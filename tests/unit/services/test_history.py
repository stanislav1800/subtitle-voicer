from subvoicer.app.bus import Bus
from subvoicer.core.events import SpeechDropped, SpeechFinished, SpeechStarted, UtteranceDetected
from subvoicer.core.models import Priority, SpeechRequest, Utterance, UtteranceKind
from subvoicer.services.history import History


def utterance(id_: int, text: str = "Привет.", speaker: str | None = "Джуди") -> Utterance:
    return Utterance("dialog", UtteranceKind.NEW, text, text, speaker, 10.0, 10.2, 0.93, "raw", id_)


def request(id_: int) -> SpeechRequest:
    return SpeechRequest(id_, "Привет.", "v1", Priority.HIGH)


def test_entry_follows_the_line() -> None:
    bus = Bus()
    history = History(50)
    history.attach(bus)
    bus.publish(UtteranceDetected(utterance(1)))
    (entry,) = history.recent()
    assert (entry.speaker, entry.text, entry.outcome) == ("Джуди", "Привет.", "queued")
    bus.publish(SpeechStarted(request(1), 1.15, 10.65))
    entry = history.get(1)
    assert entry is not None and entry.voice == "v1" and entry.rate == 1.15
    assert entry.latency_ms is not None and round(entry.latency_ms) == 650
    bus.publish(SpeechFinished(request(1), completed=False))
    assert history.get(1).outcome == "cut"  # type: ignore[union-attr]


def test_newest_first_trimmed_and_dropped() -> None:
    bus = Bus()
    history = History(3)
    history.attach(bus)
    for i in range(1, 6):
        bus.publish(UtteranceDetected(utterance(i, f"Реплика {i}.")))
    assert [e.id for e in history.recent()] == [5, 4, 3]
    bus.publish(SpeechDropped(request(4), "max_lag"))
    assert history.get(4).outcome == "dropped:max_lag"  # type: ignore[union-attr]
    bus.publish(SpeechFinished(request(99), completed=True))  # unknown id: ignored
    history.resize(1)
    assert [e.id for e in history.recent(5)] == [5]
