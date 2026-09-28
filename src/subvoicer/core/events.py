"""Events published on the bus. Services report facts here; data flows through queues."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    """Base class of all bus events. Subscribing to a base class receives its subclasses."""


@dataclass(frozen=True, slots=True)
class EngineUnavailable(Event):
    """No engine of a family could be loaded (the whole fallback chain failed)."""

    family: str
    engine_id: str
    reason: str
