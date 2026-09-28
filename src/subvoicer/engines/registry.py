"""Shared engine registry mechanism (docs/ARCHITECTURE.md, section 5).

Each family package (``engines/<family>/__init__.py``) exposes a dict
``engine id -> "module:Class"``. An engine module is imported only when resolved.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from subvoicer.core.errors import UnknownEngineError

# family -> (package, name of the registry dict in its __init__.py)
FAMILIES: dict[str, tuple[str, str]] = {
    "capture": ("subvoicer.engines.capture", "CAPTURE"),
    "ocr": ("subvoicer.engines.ocr", "OCR"),
    "tts": ("subvoicer.engines.tts", "TTS"),
    "hook": ("subvoicer.engines.hook", "HOOK"),
    "library": ("subvoicer.engines.library", "LIBRARY"),
}


def entries(family: str) -> Mapping[str, str]:
    """Return the ``engine id -> "module:Class"`` mapping of a family."""
    try:
        package, attr = FAMILIES[family]
    except KeyError:
        raise UnknownEngineError(f"Unknown engine family: {family!r}") from None
    registry: Mapping[str, str] = getattr(importlib.import_module(package), attr)
    return registry


def load_class(path: str) -> type[Any]:
    """Import ``"package.module:Class"`` and return the class."""
    module_name, sep, class_name = path.partition(":")
    if not sep or not module_name or not class_name:
        raise ValueError(f"Engine path must look like 'module:Class', got {path!r}")
    cls = getattr(importlib.import_module(module_name), class_name)
    if not isinstance(cls, type):
        raise TypeError(f"{path!r} is not a class")
    return cls


def resolve(family: str, engine_id: str) -> type[Any]:
    """Return the engine class registered as ``engine_id`` in ``family``."""
    registry = entries(family)
    try:
        path = registry[engine_id]
    except KeyError:
        known = ", ".join(sorted(registry)) or "none"
        raise UnknownEngineError(
            f"Unknown {family} engine {engine_id!r} (registered: {known})"
        ) from None
    return load_class(path)
