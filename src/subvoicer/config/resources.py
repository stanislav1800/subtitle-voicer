"""Loading default data files from the ``subvoicer.resources`` package."""

from __future__ import annotations

from importlib.resources import files


def read_text(name: str) -> str:
    return files("subvoicer.resources").joinpath(name).read_text(encoding="utf-8")


def read_list(name: str) -> list[str]:
    """Non-empty lines of a list file; ``#`` starts a comment line."""
    lines = (line.strip() for line in read_text(name).splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def short_words() -> list[str]:
    return read_list("short_words.txt")
