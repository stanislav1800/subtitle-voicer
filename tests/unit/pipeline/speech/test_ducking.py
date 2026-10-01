import pytest

from subvoicer.pipeline.speech.ducking import DuckEnvelope


def test_fades_down_while_a_line_is_on() -> None:
    env = DuckEnvelope(level=0.4, fade_s=0.15, release_s=0.3)
    assert env.update(True, 10.0) == 1.0
    assert env.update(True, 10.075) == pytest.approx(0.7)
    assert env.update(True, 10.15) == pytest.approx(0.4)
    assert env.update(True, 20.0) == pytest.approx(0.4)


def test_returns_after_release_delay_then_fades_up() -> None:
    env = DuckEnvelope(level=0.4, fade_s=0.15, release_s=0.3)
    env.update(True, 0.0)
    env.update(True, 1.0)
    assert env.update(False, 1.0) == pytest.approx(0.4)
    assert env.update(False, 1.29) == pytest.approx(0.4)
    assert env.update(False, 1.375) == pytest.approx(0.7)  # release at 1.3, half of the fade
    assert env.update(False, 1.45) == 1.0
    assert env.idle(1.45)


def test_short_gap_between_lines_keeps_the_game_ducked() -> None:
    env = DuckEnvelope(level=0.4, fade_s=0.15, release_s=0.3)
    env.update(True, 0.0)
    env.update(False, 1.0)
    assert env.update(True, 1.2) == pytest.approx(0.4)
    assert env.update(False, 1.4) == pytest.approx(0.4)  # a new release starts from here


def test_line_during_fade_up_goes_down_from_where_it_is() -> None:
    env = DuckEnvelope(level=0.4, fade_s=0.15, release_s=0.0)
    env.update(True, 0.0)
    env.update(False, 1.0)
    assert env.update(False, 1.075) == pytest.approx(0.7)
    assert env.update(True, 1.075) == pytest.approx(0.7)
    assert env.update(True, 1.15) == pytest.approx(0.4)  # 0.3 down takes half the fade


def test_nothing_happens_without_lines() -> None:
    env = DuckEnvelope()
    assert env.update(False, 5.0) == 1.0
    assert env.idle(5.0)


def test_bad_level() -> None:
    with pytest.raises(ValueError):
        DuckEnvelope(level=1.5)
