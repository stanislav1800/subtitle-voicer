"""Capture recovery policy (TZ 4.1), driven by poll(now) with fake sources."""

from __future__ import annotations

import pytest

from subvoicer.core.events import (
    CaptureFailed,
    CaptureLooksBlack,
    CaptureModeChanged,
    CaptureRestored,
    CaptureStopped,
)
from subvoicer.core.models import Frame
from subvoicer.services.capture_service import CaptureService, lit_share
from tests.fakes.capture import FakeFrameSource, RecordingBus, solid_frame
from tests.fakes.clock import FakeClock

HWND = 42


def lit_patch(share: float) -> Frame:
    """A black screen with a lit rectangle on ``share`` of it: a notification over a fade."""
    frame = solid_frame(0, 0.0, size=(100, 100))
    side = int(100 * share**0.5)
    frame.image[:side, :side, :3] = 200
    return frame


class Setup:
    def __init__(
        self,
        *,
        fail_starts: int = 0,
        dxgi_value: int | None = None,
        dxgi_frame: Frame | None = None,
        modes: tuple[str, ...] | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.bus = RecordingBus()
        self.window_open = True
        self.wgc: list[FakeFrameSource] = []
        self.dxgi: list[FakeFrameSource] = []
        self.wgc_probe: int | None = None  # a new WGC session shows this frame at once
        self._fail_starts = fail_starts
        self._dxgi_frame = dxgi_frame
        if dxgi_value is not None:
            self._dxgi_frame = solid_frame(dxgi_value, 0.0)
        makers = {"wgc": self._make_wgc, "dxgi": self._make_dxgi}
        if modes is None:
            modes = ("wgc", "dxgi") if self._dxgi_frame is not None else ("wgc",)
        factories = {mode: makers[mode] for mode in modes}
        self.service = CaptureService(
            HWND, factories, self.clock, lambda _: self.window_open, self.bus
        )

    def _make_wgc(self) -> FakeFrameSource:
        source = FakeFrameSource(fail_start=len(self.wgc) < self._fail_starts)
        if self.wgc_probe is not None:
            source.push(solid_frame(self.wgc_probe, self.clock.now()))
        self.wgc.append(source)
        return source

    def _make_dxgi(self) -> FakeFrameSource:
        source = FakeFrameSource()
        source.push(self._dxgi_frame or solid_frame(120, 0.0))
        self.dxgi.append(source)
        return source

    def poll(self, advance: float = 0.1) -> None:
        self.clock.advance(advance)
        self.service.poll(self.clock.now())

    def deliver(self, value: int = 128) -> None:
        self.wgc[-1].push(solid_frame(value, self.clock.now()))


def test_starts_and_serves_latest_frame() -> None:
    s = Setup()
    s.poll()
    assert s.wgc[0].started_hwnd == HWND
    s.deliver()
    s.poll()
    assert s.service.latest() is s.wgc[0].frame


def test_failed_start_retries_after_1s() -> None:
    s = Setup(fail_starts=1)
    s.poll()
    assert len(s.wgc) == 1
    s.poll(0.5)
    assert len(s.wgc) == 1  # still waiting
    s.poll(0.6)
    assert len(s.wgc) == 2
    s.deliver()
    s.poll()
    assert s.bus.of(CaptureFailed) == []


def test_five_failures_notify_once_then_retry_every_10s() -> None:
    s = Setup(fail_starts=100)
    s.poll()
    for _ in range(4):
        s.poll(1.0)
    assert len(s.wgc) == 5
    assert s.bus.of(CaptureFailed) == [CaptureFailed(HWND, 5)]
    s.poll(5.0)
    assert len(s.wgc) == 5
    s.poll(5.1)
    assert len(s.wgc) == 6
    s.poll(10.1)
    assert len(s.wgc) == 7
    assert len(s.bus.of(CaptureFailed)) == 1


def test_restore_after_reported_failure() -> None:
    s = Setup(fail_starts=5)
    s.poll()
    for _ in range(4):
        s.poll(1.0)
    s.poll(10.1)  # 6th attempt succeeds
    s.deliver()
    s.poll()
    assert s.bus.of(CaptureRestored) == [CaptureRestored(HWND)]


def test_session_closed_is_recreated() -> None:
    s = Setup()
    s.poll()
    s.deliver()
    s.poll()
    s.wgc[0].alive = False  # e.g. after sleep / resolution change
    s.poll()
    assert s.wgc[0].stopped
    s.poll(1.1)
    assert len(s.wgc) == 2


def test_no_first_frame_counts_as_failure() -> None:
    s = Setup()
    s.poll()
    s.poll(2.1)
    assert s.wgc[0].stopped
    s.poll(1.1)
    assert len(s.wgc) == 2


def test_stale_session_is_recreated_without_counting_a_failure() -> None:
    s = Setup()
    s.poll()
    s.deliver()
    s.poll()
    s.poll(5.1)
    assert s.wgc[0].stopped
    s.poll()
    assert len(s.wgc) == 2
    s.deliver()
    s.poll()
    assert s.bus.events == []


def test_window_closed_stops_capture() -> None:
    s = Setup()
    s.poll()
    s.deliver()
    s.poll()
    s.window_open = False
    s.poll()
    assert s.bus.of(CaptureStopped) == [CaptureStopped(HWND)]
    assert s.wgc[0].stopped
    s.poll(20)
    assert len(s.wgc) == 1


def feed_black(s: Setup, count: int) -> None:
    for _ in range(count):
        s.clock.advance(0.05)
        s.deliver(0)
        s.service.poll(s.clock.now())


def test_black_wgc_with_content_on_screen_switches_to_dxgi() -> None:
    s = Setup(dxgi_value=120)
    s.poll()
    feed_black(s, 10)
    assert s.service.mode == "dxgi"
    assert s.bus.of(CaptureModeChanged) == [CaptureModeChanged(HWND, "dxgi")]
    s.poll()
    assert len(s.dxgi) == 2  # probe + the real session
    assert s.dxgi[-1].started_hwnd == HWND


def test_black_scene_stays_on_wgc() -> None:
    s = Setup(dxgi_value=0)  # the screen is black too: a fade, not a capture problem
    s.poll()
    feed_black(s, 10)
    assert s.service.mode == "wgc"
    assert s.dxgi[0].stopped


def test_black_frames_must_be_consecutive() -> None:
    s = Setup(dxgi_value=120)
    s.poll()
    feed_black(s, 9)
    s.clock.advance(0.05)
    s.deliver(100)  # one normal frame resets the count
    s.service.poll(s.clock.now())
    feed_black(s, 9)
    assert s.dxgi == []
    assert s.service.mode == "wgc"


def test_a_notification_over_a_black_scene_is_not_a_reason_for_the_screen() -> None:
    """The screen probe sees our toast or a cursor over a fade: a couple of percent lit."""
    s = Setup(dxgi_frame=lit_patch(0.02))
    s.poll()
    feed_black(s, 10)
    assert s.service.mode == "wgc"
    s = Setup(dxgi_frame=lit_patch(0.3))  # the game itself is on the screen
    s.poll()
    feed_black(s, 10)
    assert s.service.mode == "dxgi"


def feed_dxgi(s: Setup, seconds: float, step: float = 0.5) -> None:
    for _ in range(int(seconds / step)):
        s.clock.advance(step)
        s.dxgi[-1].push(solid_frame(120, s.clock.now()))
        s.service.poll(s.clock.now())


def switch_to_screen(s: Setup) -> None:
    s.poll()
    feed_black(s, 10)
    s.poll()  # the screen session starts
    assert s.service.mode == "dxgi"


def test_back_to_the_window_once_it_shows_a_picture() -> None:
    """A dark scene of a borderless game switched to the screen: the window is tried every
    30 s, and its picture brings the capture back."""
    s = Setup(dxgi_value=120)
    switch_to_screen(s)
    s.wgc_probe = 0  # still black (exclusive fullscreen or the dark scene goes on)
    feed_dxgi(s, 31)
    assert s.service.mode == "dxgi"
    s.wgc_probe = 90
    feed_dxgi(s, 10)
    assert s.service.mode == "dxgi"  # not before 30 s since the last try
    feed_dxgi(s, 21)
    assert s.service.mode == "wgc"
    assert s.bus.of(CaptureModeChanged) == [
        CaptureModeChanged(HWND, "dxgi"),
        CaptureModeChanged(HWND, "wgc"),
    ]
    s.poll()
    assert s.wgc[-1].started_hwnd == HWND and not s.wgc[-1].stopped


def test_window_only_never_switches_but_hints_once() -> None:
    s = Setup(modes=("wgc",))
    s.poll()
    for _ in range(25):
        feed_black(s, 1)
        s.clock.advance(1.0)
    assert s.bus.of(CaptureLooksBlack) == []
    for _ in range(20):
        s.clock.advance(1.0)
        s.deliver(0)
        s.service.poll(s.clock.now())
    assert s.bus.of(CaptureLooksBlack) == [CaptureLooksBlack(HWND)]
    assert s.service.mode == "wgc" and s.dxgi == []


def test_window_only_hint_needs_black_all_the_time() -> None:
    s = Setup(modes=("wgc",))
    s.poll()
    for second in range(60):
        s.clock.advance(1.0)
        s.deliver(0 if second % 20 else 100)  # a lit frame every 20 s: a loading screen
        s.service.poll(s.clock.now())
    assert s.bus.of(CaptureLooksBlack) == []


def test_screen_only_starts_on_the_screen() -> None:
    s = Setup(modes=("dxgi",))
    s.poll()
    assert s.service.mode == "dxgi"
    assert s.dxgi[0].started_hwnd == HWND and s.wgc == []
    feed_dxgi(s, 60)
    assert s.wgc == [] and s.bus.events == []


def test_lit_share() -> None:
    assert lit_share(lit_patch(0.25)) == pytest.approx(0.25, abs=0.03)
    assert lit_share(solid_frame(0, 0.0)) == 0.0
