from dataclasses import dataclass

import pytest

from subvoicer.app.bus import Bus
from subvoicer.core.events import EngineUnavailable, Event
from subvoicer.core.ports import EventBus


@dataclass(frozen=True, slots=True)
class Ping(Event):
    n: int


def test_bus_implements_port() -> None:
    bus: EventBus = Bus()
    bus.publish(Ping(1))


def test_subscriber_receives_events_of_its_type_only() -> None:
    bus = Bus()
    pings: list[Ping] = []
    bus.subscribe(Ping, pings.append)
    bus.publish(Ping(1))
    bus.publish(EngineUnavailable("tts", "silero", "нет модели"))
    assert pings == [Ping(1)]


def test_base_class_subscriber_receives_subclasses() -> None:
    bus = Bus()
    seen: list[Event] = []
    bus.subscribe(Event, seen.append)
    bus.publish(Ping(1))
    bus.publish(EngineUnavailable("tts", "silero", "нет модели"))
    assert len(seen) == 2


def test_failing_handler_does_not_break_others(caplog: pytest.LogCaptureFixture) -> None:
    bus = Bus()
    received: list[Ping] = []

    def boom(_: Ping) -> None:
        raise RuntimeError("boom")

    bus.subscribe(Ping, boom)
    bus.subscribe(Ping, received.append)
    bus.publish(Ping(1))
    assert received == [Ping(1)]
    assert "Ping" in caplog.text


def test_unsubscribe() -> None:
    bus = Bus()
    received: list[Ping] = []
    bus.subscribe(Ping, received.append)
    bus.unsubscribe(Ping, received.append)
    bus.publish(Ping(1))
    assert received == []
