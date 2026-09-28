"""Project exception hierarchy."""


class SubvoicerError(Exception):
    """Base class of all project errors."""


class EngineError(SubvoicerError):
    """An engine failed to load or run."""


class UnknownEngineError(EngineError):
    """No engine with the requested id is registered in the family."""
