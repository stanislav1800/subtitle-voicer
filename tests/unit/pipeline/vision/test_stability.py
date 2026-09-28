"""Every row of the TZ 4.4 state table."""

import numpy as np

from subvoicer.pipeline.vision.stability import (
    Emptied,
    RunOcr,
    StabilizationSettings,
    ZoneStabilizer,
    ZoneState,
)

H, W = 50, 200


def empty() -> np.ndarray:
    return np.zeros((H, W), dtype=np.uint8)


def text(width: int = 100) -> np.ndarray:
    """A mask with a 'text' block; a wider block = more characters typed."""
    mask = empty()
    mask[10:40, 10 : 10 + width] = 255
    return mask


def test_starts_empty_with_was_empty() -> None:
    s = ZoneStabilizer()
    assert s.state is ZoneState.EMPTY
    assert s.was_empty


def test_empty_stays_empty() -> None:
    s = ZoneStabilizer()
    assert s.update(empty(), 0.0) is None
    assert s.state is ZoneState.EMPTY


def test_empty_to_changing_when_text_appears() -> None:
    s = ZoneStabilizer()
    assert s.update(text(), 1.0) is None
    assert s.state is ZoneState.CHANGING


def test_changing_runs_ocr_once_after_stable_ms() -> None:
    s = ZoneStabilizer(StabilizationSettings(stable_ms=200))
    s.update(text(), 1.0)
    assert s.update(text(), 1.1) is None  # stable 100 ms
    decision = s.update(text(), 1.2)  # stable 200 ms
    assert decision == RunOcr(t_change=1.0)
    assert s.state is ZoneState.SHOWN
    assert s.update(text(), 1.3) is None  # no second OCR for the same text
    assert s.update(text(), 5.0) is None


def test_change_in_changing_restarts_stable_timer() -> None:
    s = ZoneStabilizer(StabilizationSettings(stable_ms=200, max_wait_ms=10_000))
    s.update(text(50), 1.0)
    s.update(text(80), 1.15)  # typing continues
    assert s.update(text(80), 1.3) is None  # only 150 ms since the last change
    assert s.update(text(80), 1.35) == RunOcr(t_change=1.0)


def test_max_wait_forces_ocr_while_mask_keeps_changing() -> None:
    s = ZoneStabilizer(StabilizationSettings(stable_ms=200, max_wait_ms=1500))
    s.update(text(20), 0.0)
    decisions = [s.update(text(20 + i * 5), i * 0.1) for i in range(1, 16)]
    assert decisions[:-1] == [None] * 14
    assert decisions[-1] == RunOcr(t_change=0.0)  # at 1.5 s


def test_changing_to_empty_sets_was_empty() -> None:
    s = ZoneStabilizer()
    s.update(text(), 1.0)
    s.consume_was_empty()
    assert s.update(empty(), 1.1) == Emptied()
    assert s.state is ZoneState.EMPTY
    assert s.was_empty


def test_shown_to_empty_sets_was_empty() -> None:
    s = ZoneStabilizer()
    s.update(text(), 1.0)
    s.update(text(), 1.3)
    s.consume_was_empty()
    assert s.update(empty(), 2.0) == Emptied()
    assert s.was_empty


def test_shown_to_changing_on_mask_change() -> None:
    s = ZoneStabilizer()
    s.update(text(50), 1.0)
    s.update(text(50), 1.3)
    assert s.state is ZoneState.SHOWN
    assert s.update(text(150), 2.0) is None
    assert s.state is ZoneState.CHANGING
    assert s.update(text(150), 2.2) == RunOcr(t_change=2.0)


def test_shown_compares_with_recognised_mask_not_previous_frame() -> None:
    """A slow fade: +20 px per frame is below the threshold (0.5% of 10000 px = 50 px) frame
    to frame, but against the recognised mask it adds up to a change on the third frame."""
    s = ZoneStabilizer()
    mask = text(100)
    s.update(mask, 1.0)
    s.update(mask, 1.3)
    assert s.state is ZoneState.SHOWN
    for frame in range(1, 4):
        mask = mask.copy()
        mask[42:44, 10 * frame : 10 * frame + 10] = 255  # +20 px
        s.update(mask, 1.3 + frame * 0.1)
        expected = ZoneState.CHANGING if frame == 3 else ZoneState.SHOWN
        assert s.state is expected, frame


def test_consume_was_empty() -> None:
    s = ZoneStabilizer()
    s.consume_was_empty()
    assert not s.was_empty
