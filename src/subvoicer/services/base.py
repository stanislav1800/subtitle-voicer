"""Service: a daemon thread that calls ``step()`` periodically and never dies of an exception."""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)


class Service:
    def __init__(self, name: str, interval_s: float) -> None:
        self.name = name
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> None:
        raise NotImplementedError

    def on_stop(self) -> None:
        """Called in the service thread after the loop ends."""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)

    @property
    def running(self) -> bool:
        return self._thread is not None and not self._stop.is_set()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.step()
            except Exception:
                log.exception("Сервис %s: ошибка шага, продолжаю", self.name)
            self._stop.wait(max(0.0, self.interval_s - (time.monotonic() - started)))
        try:
            self.on_stop()
        except Exception:
            log.exception("Сервис %s: ошибка при остановке", self.name)
