"""Keeps a FrameSource alive for one window (TZ 4.1): recovery, and by the profile's capture
mode — the window only, the screen only, or the window with a fallback to the screen while
the window gives black frames (and back once it shows a picture again).

The policy lives in ``poll(now)`` so tests can drive it with fake sources and a fake clock;
the service thread just calls it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from subvoicer.core.events import (
    CaptureFailed,
    CaptureLooksBlack,
    CaptureModeChanged,
    CaptureRestored,
    CaptureStopped,
)
from subvoicer.core.models import Frame
from subvoicer.core.ports import Clock, EventPublisher, FrameSource
from subvoicer.services.base import Service

log = logging.getLogger(__name__)

WGC, DXGI = "wgc", "dxgi"


@dataclass(frozen=True, slots=True)
class CaptureSettings:
    retry_delay_s: float = 1.0
    max_attempts: int = 5
    slow_retry_s: float = 10.0
    first_frame_timeout_s: float = 2.0
    stale_after_s: float = 5.0  # no frames this long -> quietly recreate the session
    black_frames: int = 10
    black_max: int = 5  # a frame is black when max brightness < this
    probe_timeout_s: float = 1.0
    # the screen shows the game, not a window over a black scene: this share of it is lit
    content_share: float = 0.05
    back_to_window_s: float = 30.0  # on the screen fallback: how often to try the window
    black_hint_s: float = 30.0  # window only: black this long -> CaptureLooksBlack


def is_black(frame: Frame, black_max: int = 5) -> bool:
    return int(frame.image[::4, ::4, :3].max()) < black_max


def lit_share(frame: Frame, black_max: int = 5) -> float:
    """Share of not black pixels (sampled): a notification or a cursor over a black scene is
    a percent or two of the frame, a game picture is much more."""
    sample = frame.image[::4, ::4, :3]
    return float((sample.max(axis=2) >= black_max).mean())


class CaptureService(Service):
    def __init__(
        self,
        hwnd: int,
        factories: Mapping[str, Callable[[], FrameSource]],
        clock: Clock,
        window_alive: Callable[[int], bool],
        publisher: EventPublisher,
        settings: CaptureSettings | None = None,
        interval_s: float = 0.1,
    ) -> None:
        super().__init__("CaptureService", interval_s)
        self.hwnd = hwnd
        self.settings = settings or CaptureSettings()
        self.mode = next(iter(factories))  # the first mode of the profile's capture setting
        self._factories = factories
        self._clock = clock
        self._window_alive = window_alive
        self._publisher = publisher
        self._lock = threading.Lock()
        self._source: FrameSource | None = None
        self._failures = 0
        self._reported_failure = False
        self._next_attempt_at = 0.0
        self._awaiting_first = False
        self._started_at = 0.0
        self._last_frame_ts: float | None = None
        self._last_frame_at = 0.0
        self._black_count = 0
        self._black_since: float | None = None
        self._hinted = False
        self._window_tried_at = 0.0
        self.stopped = False

    def latest(self) -> Frame | None:
        with self._lock:
            source = self._source
        return source.latest() if source is not None else None

    def step(self) -> None:
        self.poll(self._clock.now())

    def on_stop(self) -> None:
        self._drop_source()

    def poll(self, now: float) -> None:
        if self.stopped:
            return
        if not self._window_alive(self.hwnd):
            log.info("Окно %s закрыто — захват остановлен", self.hwnd)
            self._drop_source()
            self.stopped = True
            self._publisher.publish(CaptureStopped(self.hwnd))
            return

        if self._source is None:
            if now >= self._next_attempt_at:
                self._try_start(now)
            return

        frame = self._source.latest()
        if frame is not None and frame.timestamp != self._last_frame_ts:
            self._on_new_frame(frame, now)
            if self._source is None:  # switched the mode: the new session starts next poll
                return

        if not self._source.is_alive():
            self._fail(now, "сессия захвата закрылась")
        elif self._awaiting_first:
            if now - self._started_at > self.settings.first_frame_timeout_s:
                self._fail(now, "нет первого кадра")
        elif now - self._last_frame_at > self.settings.stale_after_s:
            log.debug("Нет кадров %.0f с — пересоздаю захват", self.settings.stale_after_s)
            self._drop_source()
            self._next_attempt_at = now

    # ------------------------------------------------------------------ internals

    def _on_new_frame(self, frame: Frame, now: float) -> None:
        self._last_frame_ts = frame.timestamp
        self._last_frame_at = now
        if self._awaiting_first:
            self._awaiting_first = False
            if self._reported_failure:
                log.info("Захват окна восстановлен")
                self._publisher.publish(CaptureRestored(self.hwnd))
            self._failures = 0
            self._reported_failure = False
        fallback = WGC in self._factories and DXGI in self._factories
        if self.mode == WGC and fallback:
            self._black_count = (
                self._black_count + 1 if is_black(frame, self.settings.black_max) else 0
            )
            if self._black_count >= self.settings.black_frames:
                self._black_count = 0
                if self._shows_picture(DXGI):
                    log.info(
                        "WGC отдаёт чёрные кадры, а на экране есть изображение — перехожу на DXGI"
                    )
                    self._switch(DXGI, now)
        elif self.mode == WGC:
            self._watch_black(frame, now)
        elif fallback and now - self._window_tried_at >= self.settings.back_to_window_s:
            self._window_tried_at = now
            if self._shows_picture(WGC):
                log.info("Захват окна снова показывает изображение — возвращаюсь на WGC")
                self._switch(WGC, now)

    def _switch(self, mode: str, now: float) -> None:
        self.mode = mode
        self._window_tried_at = now
        self._drop_source()
        self._next_attempt_at = now
        self._publisher.publish(CaptureModeChanged(self.hwnd, mode))

    def _watch_black(self, frame: Frame, now: float) -> None:
        """Window only: no fallback, a hint once if the frames stay black for long."""
        if not is_black(frame, self.settings.black_max):
            self._black_since = None
            return
        if self._black_since is None:
            self._black_since = now
        elif not self._hinted and now - self._black_since >= self.settings.black_hint_s:
            self._hinted = True
            log.warning("Захват окна %.0f с отдаёт чёрные кадры", now - self._black_since)
            self._publisher.publish(CaptureLooksBlack(self.hwnd))

    def _shows_picture(self, mode: str) -> bool:
        """A probe session of ``mode``: does its first frame show the game? Black WGC frames
        may be a genuinely black scene (a fade) — then the screen is black too, but for a
        notification or a cursor over it; so a lit share of the frame, not one pixel."""
        probe = self._factories[mode]()
        try:
            probe.start(self.hwnd)
            deadline = time.monotonic() + self.settings.probe_timeout_s
            while time.monotonic() < deadline:
                frame = probe.latest()
                if frame is not None:
                    return lit_share(frame, self.settings.black_max) >= self.settings.content_share
                time.sleep(0.02)
            return False
        except Exception:
            log.debug("Проверка захвата %s не удалась", mode, exc_info=True)
            return False
        finally:
            probe.stop()

    def _try_start(self, now: float) -> None:
        source = self._factories[self.mode]()
        try:
            source.start(self.hwnd)
        except Exception as exc:
            log.debug("Запуск захвата %s не удался: %s", self.mode, exc)
            self._fail(now, f"запуск не удался: {exc}")
            return
        with self._lock:
            self._source = source
        self._awaiting_first = True
        self._started_at = now
        self._last_frame_at = now

    def _fail(self, now: float, reason: str) -> None:
        self._drop_source()
        self._failures += 1
        s = self.settings
        if self._failures < s.max_attempts:
            log.info(
                "Захват: %s — повтор через %.0f с (попытка %d)",
                reason,
                s.retry_delay_s,
                self._failures,
            )
            self._next_attempt_at = now + s.retry_delay_s
            return
        if not self._reported_failure:
            self._reported_failure = True
            log.warning("Не удаётся захватить окно игры (%d попыток подряд)", self._failures)
            self._publisher.publish(CaptureFailed(self.hwnd, self._failures))
        self._next_attempt_at = now + s.slow_retry_s

    def _drop_source(self) -> None:
        with self._lock:
            source, self._source = self._source, None
        self._awaiting_first = False
        if source is not None:
            try:
                source.stop()
            except Exception:
                log.debug("Ошибка остановки захвата", exc_info=True)
