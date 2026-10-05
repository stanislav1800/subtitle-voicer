"""Project exception hierarchy."""


class SubvoicerError(Exception):
    """Base class of all project errors."""


class EngineError(SubvoicerError):
    """An engine failed to load or run."""


class ModelsError(SubvoicerError):
    """Models could not be downloaded (TZ 4.14); the message is for the user."""


class UnknownEngineError(EngineError):
    """No engine with the requested id is registered in the family."""
