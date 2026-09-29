"""SpeechScheduler: every rule of TZ 4.9."""

from __future__ import annotations

import itertools

import pytest

from subvoicer.core.models import Priority, SpeechRequest
from subvoicer.pipeline.speech.scheduler import (
    Command,
    Dropped,
    DropReason,
    Hurry,
    OverlapMode,
    Play,
    SpeechScheduler,
    Stop,
    speedup,
)

H, N, L, M = Priority.HIGH, Priority.NORMAL, Priority.LOW, Priority.MANUAL
_ids = itertools.count(1)


def req(
    priority: Priority = H, channel: str = "dialog", *, continuation: bool = False
) -> SpeechRequest:
    return SpeechRequest(
        id=next(_ids),
        text="текст",
        voice_id="ru_voice",
        priority=priority,
        continuation=continuation,
        channel=channel,
    )


def played(commands: list[Command]) -> list[SpeechRequest]:
    return [c.request for c in commands if isinstance(c, Play)]


def stopped(commands: list[Command]) -> list[SpeechRequest]:
    return [c.request for c in commands if isinstance(c, Stop)]


def dropped(commands: list[Command]) -> list[tuple[SpeechRequest, DropReason]]:
    return [(c.request, c.reason) for c in commands if isinstance(c, Dropped)]


@pytest.fixture
def s() -> SpeechScheduler:
    return SpeechScheduler(base_rate=1.0, low_priority_ttl_s=5.0, overlap=OverlapMode.CUT)


def test_idle_plays_immediately(s: SpeechScheduler) -> None:
    a = req()
    assert s.submit(a, 0) == [Play(a, 1.0)]
    assert s.current is a


def test_higher_priority_interrupts(s: SpeechScheduler) -> None:
    low, high = req(L, "obj"), req(H)
    s.submit(low, 0)
    commands = s.submit(high, 1)
    assert stopped(commands) == [low]
    assert played(commands) == [high]


def test_same_priority_new_line_cuts_old(s: SpeechScheduler) -> None:
    a, b = req(H), req(H)
    s.submit(a, 0)
    commands = s.submit(b, 1)
    assert stopped(commands) == [a] and played(commands) == [b]


def test_lower_priority_waits_in_queue_then_plays(s: SpeechScheduler) -> None:
    dialog, objective = req(H), req(L, "obj")
    s.submit(dialog, 0)
    assert s.submit(objective, 1) == []
    assert s.queued == [objective]
    assert played(s.finished(dialog.id, True, 3)) == [objective]


def test_queued_low_line_expires_after_ttl(s: SpeechScheduler) -> None:
    dialog, objective = req(H), req(L, "obj")
    s.submit(dialog, 0)
    s.submit(objective, 1)
    assert s.tick(5.9) == []
    assert dropped(s.tick(6.1)) == [(objective, DropReason.EXPIRED)]
    assert s.finished(dialog.id, True, 7) == []


def test_expired_lines_are_skipped_when_the_current_ends(s: SpeechScheduler) -> None:
    dialog, old, fresh = req(H), req(L, "obj"), req(N, "notes")
    s.submit(dialog, 0)
    s.submit(old, 0)
    s.submit(fresh, 4)
    commands = s.finished(dialog.id, True, 6)
    assert dropped(commands) == [(old, DropReason.EXPIRED)]
    assert played(commands) == [fresh]


def test_queue_order_priority_then_time(s: SpeechScheduler) -> None:
    s.submit(req(H), 0)
    low1, normal, low2 = req(L, "a"), req(N, "b"), req(L, "c")
    for r in (low1, normal, low2):
        s.submit(r, 1)
    assert s.queued == [normal, low1, low2]


def test_continuation_queues_right_after_current_regardless_of_priority(
    s: SpeechScheduler,
) -> None:
    objective = req(L, "obj")
    s.submit(objective, 0)
    queued_high = req(N, "notes")
    s.submit(queued_high, 0.5)  # higher: cuts the objective and plays
    tail = req(L, "notes", continuation=True)
    assert s.submit(tail, 1) == []  # does not interrupt
    waiting = req(L, "other")
    s.submit(waiting, 1.1)
    assert s.queued[0] is tail
    assert played(s.finished(queued_high.id, True, 2)) == [tail]


def test_continuation_plays_at_once_when_idle(s: SpeechScheduler) -> None:
    a = req(H)
    s.submit(a, 0)
    s.finished(a.id, True, 1)
    tail = req(H, continuation=True)
    assert played(s.submit(tail, 1.5)) == [tail]


def test_continuation_of_a_cut_line_is_dropped(s: SpeechScheduler) -> None:
    a = req(H, "dialog")
    s.submit(a, 0)
    tail_queued = req(H, "dialog", continuation=True)
    s.submit(tail_queued, 0.5)
    b = req(H, "dialog")
    commands = s.submit(b, 1)  # the new line cuts a: its queued tail goes too
    assert (tail_queued, DropReason.INTERRUPTED) in dropped(commands)
    s.finished(b.id, True, 3)
    late_tail = req(H, "other", continuation=True)
    s.submit(req(H, "other"), 4)
    s.submit(req(H, "x"), 4.5)  # cuts "other"
    assert dropped(s.submit(late_tail, 5)) == [(late_tail, DropReason.INTERRUPTED)]


def test_line_disappearing_from_screen_does_not_stop_speech(s: SpeechScheduler) -> None:
    """The zone emptying is not an event for the scheduler: nothing stops the line."""
    a = req(H)
    s.submit(a, 0)
    assert s.tick(10) == []
    assert s.current is a


def test_manual_cuts_current_clears_queue_and_blocks_auto(s: SpeechScheduler) -> None:
    dialog, objective, manual = req(H), req(L, "obj"), req(M, "manual")
    s.submit(dialog, 0)
    s.submit(objective, 0.5)
    commands = s.submit(manual, 1)
    assert stopped(commands) == [dialog]
    assert (objective, DropReason.MANUAL) in dropped(commands)
    assert played(commands) == [manual]
    auto = req(H)
    assert dropped(s.submit(auto, 1.5)) == [(auto, DropReason.MANUAL)]  # not queued
    assert s.finished(manual.id, True, 3) == []  # nothing waiting after the manual line
    after = req(H)
    assert played(s.submit(after, 4)) == [after]


def test_manual_cuts_manual(s: SpeechScheduler) -> None:
    m1, m2 = req(M, "manual"), req(M, "manual")
    s.submit(m1, 0)
    commands = s.submit(m2, 1)
    assert stopped(commands) == [m1] and played(commands) == [m2]


def test_late_finish_of_a_cut_line_is_ignored(s: SpeechScheduler) -> None:
    a, b = req(H), req(H)
    s.submit(a, 0)
    s.submit(b, 1)
    assert s.finished(a.id, False, 1.1) == []
    assert s.current is b


def test_skip_cuts_and_moves_on(s: SpeechScheduler) -> None:
    dialog, objective = req(H), req(L, "obj")
    s.submit(dialog, 0)
    s.submit(objective, 0)
    commands = s.skip(1)
    assert stopped(commands) == [dialog] and played(commands) == [objective]
    assert s.skip(2) != []
    assert s.skip(3) == []


def test_repeat_replays_last_line_as_manual(s: SpeechScheduler) -> None:
    a = req(H)
    s.submit(a, 0)
    s.finished(a.id, True, 2)
    (play,) = s.repeat(3)
    assert isinstance(play, Play)
    assert play.request.text == a.text and play.request.voice_id == a.voice_id
    assert play.request.priority is M and play.request.id != a.id
    s.finished(play.request.id, True, 5)
    assert s.repeat(6) != []  # the repeat itself is not "last played"


def test_repeat_without_history(s: SpeechScheduler) -> None:
    assert s.repeat(0) == []


def test_pause_stops_auto_and_drops_new_lines(s: SpeechScheduler) -> None:
    dialog, objective = req(H), req(L, "obj")
    s.submit(dialog, 0)
    s.submit(objective, 0)
    commands = s.set_paused(True)
    assert stopped(commands) == [dialog]
    assert dropped(commands) == [(objective, DropReason.PAUSED)]
    late = req(H)
    assert dropped(s.submit(late, 1)) == [(late, DropReason.PAUSED)]
    manual = req(M, "manual")
    assert played(s.submit(manual, 2)) == [manual]  # manual still works
    s.finished(manual.id, True, 3)
    s.set_paused(False)
    again = req(H)
    assert played(s.submit(again, 4)) == [again]


# ------------------------------------------------------------------ auto speed-up


def test_speedup_formula() -> None:
    assert [speedup(n) for n in range(5)] == [1.0, 1.0, pytest.approx(1.15), 1.3, 1.3]


def test_two_cut_high_lines_speed_up_the_next(s: SpeechScheduler) -> None:
    rates = []
    for t in range(4):
        (play,) = [c for c in s.submit(req(H), t) if isinstance(c, Play)]
        rates.append(play.rate)
    assert rates == [1.0, 1.0, pytest.approx(1.15), pytest.approx(1.3)]


def test_completed_high_line_resets_speedup(s: SpeechScheduler) -> None:
    for t in range(3):
        s.submit(req(H), t)
    assert s.interrupts == 2
    s.finished(s.current.id, True, 5)  # type: ignore[union-attr]
    assert s.interrupts == 0
    s.submit(req(H), 6)
    assert s.current_rate == 1.0


def test_other_priorities_do_not_change_the_counter(s: SpeechScheduler) -> None:
    s.submit(req(N, "a"), 0)
    s.submit(req(N, "b"), 1)  # normal cuts normal
    s.submit(req(H), 2)  # high cuts normal
    assert s.interrupts == 0


def test_speedup_is_relative_to_base_rate() -> None:
    s = SpeechScheduler(base_rate=1.1, overlap=OverlapMode.CUT)
    for t in range(3):
        s.submit(req(H), t)
    assert s.current_rate == pytest.approx(1.1 * 1.15)


def test_continuation_keeps_the_rate_of_its_line(s: SpeechScheduler) -> None:
    for t in range(3):
        s.submit(req(H, "dialog"), t)
    assert s.current_rate == pytest.approx(1.15)
    tail = req(H, "dialog", continuation=True)
    s.submit(tail, 3)
    s.finished(s.current.id, True, 4)  # type: ignore[union-attr]
    assert s.current is tail and s.current_rate == pytest.approx(1.15)


# ------------------------------------------------------------------ overlap modes (TZ 1.14)


def hurried(commands: list[Command]) -> list[tuple[SpeechRequest, float]]:
    return [(c.request, c.rate) for c in commands if isinstance(c, Hurry)]


def mode(overlap: OverlapMode) -> SpeechScheduler:
    return SpeechScheduler(overlap=overlap, hurry_rate=1.5, catchup_rate=1.15, max_lag_s=8.0)


def test_default_mode_is_speedup() -> None:
    assert SpeechScheduler().overlap is OverlapMode.SPEEDUP


def test_queue_mode_waits_for_the_current_line() -> None:
    s = mode(OverlapMode.QUEUE)
    a, b = req(H), req(H)
    s.submit(a, 0)
    assert s.submit(b, 1) == []
    assert played(s.finished(a.id, True, 3)) == [b]
    assert s.current_rate == 1.0


def test_waiting_modes_drop_lines_that_lag_too_much() -> None:
    s = mode(OverlapMode.QUEUE)
    a, b, c = req(H), req(H), req(H)
    s.submit(a, 0)
    s.submit(b, 1)
    s.submit(c, 7)
    commands = s.finished(a.id, True, 9.5)  # b waited 8.5 s > max_lag_s
    assert dropped(commands) == [(b, DropReason.EXPIRED)]
    assert played(commands) == [c]


def test_speedup_hurries_the_current_line_and_the_next_catches_up() -> None:
    s = mode(OverlapMode.SPEEDUP)
    a, b = req(H), req(H)
    s.submit(a, 0)
    commands = s.submit(b, 1)
    assert hurried(commands) == [(a, 1.5)]
    assert stopped(commands) == [] and played(commands) == []
    (play,) = [c for c in s.finished(a.id, True, 2) if isinstance(c, Play)]
    assert play.request is b and play.rate == pytest.approx(1.15)


def test_speedup_hurries_once_and_keeps_hurrying_while_lines_wait() -> None:
    s = mode(OverlapMode.SPEEDUP)
    a, b, c = req(H), req(H), req(H)
    s.submit(a, 0)
    s.submit(b, 1)
    assert hurried(s.submit(c, 1.5)) == []  # already hurrying
    (play,) = [x for x in s.finished(a.id, True, 2) if isinstance(x, Play)]
    assert play.request is b and play.rate == pytest.approx(1.5)  # c still waits
    (play,) = [x for x in s.finished(b.id, True, 3) if isinstance(x, Play)]
    assert play.request is c and play.rate == pytest.approx(1.15)


def test_speedup_without_waiting_lines_plays_at_base_rate() -> None:
    s = mode(OverlapMode.SPEEDUP)
    a = req(H)
    assert s.submit(a, 0) == [Play(a, 1.0)]


def test_adaptive_speed_grows_with_the_queue() -> None:
    s = SpeechScheduler(overlap=OverlapMode.ADAPTIVE, adaptive_step=0.2, adaptive_max=1.6)
    a = req(H)
    s.submit(a, 0)
    assert hurried(s.submit(req(H), 1)) == [(a, pytest.approx(1.2))]
    assert hurried(s.submit(req(H), 1.1)) == [(a, pytest.approx(1.4))]
    assert hurried(s.submit(req(H), 1.2)) == [(a, pytest.approx(1.6))]
    assert hurried(s.submit(req(H), 1.3)) == []  # capped at 1.6
    (play,) = [x for x in s.finished(a.id, True, 2) if isinstance(x, Play)]
    assert play.rate == pytest.approx(1.6)  # three still waiting


def test_higher_priority_still_cuts_in_every_mode() -> None:
    for overlap in OverlapMode:
        s = mode(overlap)
        low, high = req(L, "obj"), req(H)
        s.submit(low, 0)
        commands = s.submit(high, 1)
        assert stopped(commands) == [low] and played(commands) == [high], overlap


def test_continuation_goes_before_waiting_lines_and_hurries_with_its_line() -> None:
    s = mode(OverlapMode.SPEEDUP)
    a, b = req(H, "dialog"), req(H, "dialog")
    s.submit(a, 0)
    s.submit(b, 1)  # a hurries to 1.5
    tail = req(H, "dialog", continuation=True)
    s.submit(tail, 1.2)
    assert s.queued[0] is tail
    (play,) = [x for x in s.finished(a.id, True, 2) if isinstance(x, Play)]
    assert play.request is tail and play.rate == pytest.approx(1.5)


def test_manual_line_is_not_hurried() -> None:
    s = mode(OverlapMode.SPEEDUP)
    s.submit(req(M, "manual"), 0)
    auto = req(H)
    assert dropped(s.submit(auto, 1)) == [(auto, DropReason.MANUAL)]
