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


def flicker(step: int) -> np.ndarray:
    """Text plus background blobs that come and go: pixels are added and removed."""
    mask = text(100)
    x = 120 + (step % 2) * 30
    mask[5:45, x : x + 25] = 255
    return mask


def test_max_wait_forces_ocr_while_mask_flickers() -> None:
    s = ZoneStabilizer(StabilizationSettings(stable_ms=200, max_wait_ms=1500))
    s.update(flicker(0), 0.0)
    decisions = [s.update(flicker(i), i * 0.1) for i in range(1, 16)]
    assert decisions[:-1] == [None] * 14
    assert decisions[-1] == RunOcr(t_change=0.0)  # at 1.5 s


def test_typing_longer_than_max_wait_is_read_once_when_it_stops() -> None:
    s = ZoneStabilizer(StabilizationSettings(stable_ms=200, max_wait_ms=1500))
    s.update(text(4), 0.0)
    decisions = [s.update(text(4 + i * 4), i * 0.1) for i in range(1, 31)]  # 3 s of typing
    assert decisions == [None] * 30
    assert s.update(text(124), 3.1) is None
    assert s.update(text(124), 3.2) == RunOcr(t_change=0.0)


def test_typing_is_forced_after_typing_max_wait() -> None:
    s = ZoneStabilizer(StabilizationSettings(max_wait_ms=1500, typing_max_wait_ms=5000))
    s.update(text(2), 0.0)
    decisions = [s.update(text(2 + i * 3), i * 0.1) for i in range(1, 51)]
    assert decisions[:-1] == [None] * 49
    assert decisions[-1] == RunOcr(t_change=0.0, still_typing=True)  # at 5 s
    # still CHANGING: once typing stops the zone is read again
    assert s.state is ZoneState.CHANGING
    assert s.update(text(152), 5.1) is None
    assert s.update(text(152), 5.3) == RunOcr(t_change=5.0)
    assert s.state is ZoneState.SHOWN


def test_typing_that_starts_flickering_falls_back_to_max_wait() -> None:
    s = ZoneStabilizer(StabilizationSettings(max_wait_ms=1500))
    s.update(text(10), 0.0)
    s.update(text(20), 0.1)
    s.update(flicker(1), 0.2)  # pixels removed too
    decisions = [s.update(flicker(i), 0.2 + i * 0.1) for i in range(2, 14)]
    assert RunOcr(t_change=0.0) in decisions


def test_continuation_of_shown_text_counts_as_typing() -> None:
    s = ZoneStabilizer(StabilizationSettings(max_wait_ms=1500))
    s.update(text(40), 0.0)
    s.update(text(40), 0.2)
    assert s.state is ZoneState.SHOWN
    decisions = [s.update(text(40 + i * 3), 1.0 + i * 0.1) for i in range(1, 25)]
    assert decisions == [None] * 24  # 2.4 s of typing more, still waiting


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
    """A slow drift: each frame moves 10 px (20 px changed, below 0.5% of 10000 px = 50 px),
    but against the recognised mask it adds up to a change on the third frame."""
    s = ZoneStabilizer()
    mask = text(100)
    s.update(mask, 1.0)
    s.update(mask, 1.3)
    assert s.state is ZoneState.SHOWN
    for frame in range(1, 4):
        mask = mask.copy()
        mask[10, 10 + 10 * frame : 20 + 10 * frame] = 0  # 10 px disappear
        mask[45, 10 + 10 * frame : 20 + 10 * frame] = 255  # 10 px appear
        s.update(mask, 1.3 + frame * 0.1)
        expected = ZoneState.CHANGING if frame == 3 else ZoneState.SHOWN
        assert s.state is expected, frame


def test_one_typed_character_is_a_change_while_changing() -> None:
    """One character is far below change_ratio, but typing must keep the zone CHANGING."""
    s = ZoneStabilizer(StabilizationSettings(stable_ms=200))
    mask = text(20)
    s.update(mask, 0.0)
    for step in range(1, 8):  # +1 character (3x10 px) every 150 ms
        mask = mask.copy()
        mask[20:30, 30 + step * 4 : 33 + step * 4] = 255
        assert s.update(mask, step * 0.15) is None, step
    assert s.update(mask, 1.05 + 0.2) == RunOcr(t_change=0.0)


def test_edge_jitter_is_not_typing() -> None:
    s = ZoneStabilizer(StabilizationSettings(stable_ms=200))
    base = text(100)
    s.update(base, 0.0)
    jitter = base.copy()
    jitter[10, 10:25] = 0  # 15 px lost
    jitter[40, 10:25] = 255  # 15 px gained
    assert s.update(jitter, 0.1) is None
    assert s.update(base, 0.2) == RunOcr(t_change=0.0)


def test_consume_was_empty() -> None:
    s = ZoneStabilizer()
    s.consume_was_empty()
    assert not s.was_empty
