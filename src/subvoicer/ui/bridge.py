"""Bus events -> a Qt signal delivered in the GUI thread (services publish from their threads)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from subvoicer.app.facade import AppFacade
from subvoicer.core.events import Event


class Bridge(QObject):
    event = Signal(object)  # any bus Event; queued into the GUI thread by Qt

    def __init__(self, facade: AppFacade) -> None:
        super().__init__()
        facade.subscribe(Event, self.event.emit)
