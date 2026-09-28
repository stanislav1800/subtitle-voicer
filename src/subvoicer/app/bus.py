"""Thread-safe publish/subscribe event bus (docs/ARCHITECTURE.md, section 6)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from subvoicer.core.events import Event

log = logging.getLogger(__name__)

E = TypeVar("E", bound=Event)


class Bus:
    """Delivers events to subscribers synchronously, in the publisher's thread.

    A handler subscribed to a base event class also receives its subclasses.
    A failing handler is logged and does not affect other handlers or the publisher.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[tuple[type[Event], Callable[[Any], None]]] = []

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        with self._lock:
            self._handlers.append((event_type, handler))

    def unsubscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        with self._lock:
            self._handlers.remove((event_type, handler))

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = [h for t, h in self._handlers if isinstance(event, t)]
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception("Обработчик события %s упал", type(event).__name__)
