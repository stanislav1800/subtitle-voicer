import pytest

from subvoicer.core.ports import Clock
from tests.fakes.clock import FakeClock


def test_fake_clock_moves_only_on_demand() -> None:
    clock: Clock = FakeClock(start=10.0)
    assert clock.now() == 10.0
    assert clock.now() == 10.0


def test_fake_clock_advance() -> None:
    clock = FakeClock()
    clock.advance(1.5)
    clock.advance_ms(250)
    assert clock.now() == pytest.approx(1.75)


def test_fake_clock_cannot_go_backwards() -> None:
    with pytest.raises(ValueError):
        FakeClock().advance(-1)
