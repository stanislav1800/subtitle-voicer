"""Capture recovery policy (TZ 4.1), driven by poll(now) with fake sources."""

from __future__ import annotations

from subvoicer.core.events import (
    CaptureFailed,
    CaptureModeChanged,
    CaptureRestored,
    CaptureStopped,
)
from subvoicer.services.capture_service import CaptureService
from tests.fakes.capture import FakeFrameSource, RecordingBus, solid_frame
from tests.fakes.clock import FakeClock

HWND = 42


class Setup:
    def __init__(self, *, fail_starts: int = 0, dxgi_value: int | None = None) -> None:
        self.clock = FakeClock()
        self.bus = RecordingBus()
        self.window_open = True
        self.wgc: list[FakeFrameSource] = []
        self.dxgi: list[FakeFrameSource] = []
        self._fail_starts = fail_starts
        self._dxgi_value = dxgi_value
        factories = {"wgc": self._make_wgc}
        if dxgi_value is not None:
            factories["dxgi"] = self._make_dxgi
        self.service = CaptureService(
            HWND, factories, self.clock, lambda _: self.window_open, self.bus
        )

    def _make_wgc(self) -> FakeFrameSource:
        source = FakeFrameSource(fail_start=len(self.wgc) < self._fail_starts)
        self.wgc.append(source)
        return source

    def _make_dxgi(self) -> FakeFrameSource:
        source = FakeFrameSource()
        assert self._dxgi_value is not None
        source.push(solid_frame(self._dxgi_value, 0.0))
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
