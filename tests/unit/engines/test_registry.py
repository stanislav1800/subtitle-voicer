from collections import OrderedDict

import pytest

from subvoicer.core.errors import UnknownEngineError
from subvoicer.engines import registry, tts


def test_every_family_has_a_registry_dict() -> None:
    for family in registry.FAMILIES:
        assert isinstance(registry.entries(family), dict)


def test_resolve_imports_class_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(tts.TTS, "fake", "collections:OrderedDict")
    assert registry.resolve("tts", "fake") is OrderedDict


def test_unknown_engine_lists_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(tts.TTS, "fake", "collections:OrderedDict")
    with pytest.raises(UnknownEngineError, match="fake"):
        registry.resolve("tts", "missing")


def test_unknown_family() -> None:
    with pytest.raises(UnknownEngineError):
        registry.resolve("nope", "x")


@pytest.mark.parametrize("path", ["collections", "collections:", ":OrderedDict"])
def test_bad_path(path: str) -> None:
    with pytest.raises(ValueError):
        registry.load_class(path)


def test_path_must_point_to_class() -> None:
    with pytest.raises(TypeError):
        registry.load_class("os.path:join")
