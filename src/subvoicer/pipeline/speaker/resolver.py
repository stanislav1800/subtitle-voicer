"""SpeakerResolver: who says the line and what is left to voice (TZ 4.5)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from subvoicer.pipeline.speaker.extract import DEFAULT_PREFIX_PATTERN, extract_prefix
from subvoicer.pipeline.speaker.validate import NameValidator

# "…кто? Джеки: Вылезай." — a name after the end of a sentence starts another speaker's line
INNER_NAME = re.compile(r"(?<=[.!?…»\"])\s+([^\s:.!?…][^:.!?…]{0,29}):\s+")


class SpeakerMode(StrEnum):
    PREFIX = "prefix"
    ZONE = "zone"
    COLOR = "color"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SpeakerResult:
    name: str | None
    text: str  # the line without an accepted name prefix


class SpeakerResolver:
    def __init__(
        self,
        mode: SpeakerMode,
        stoplist: Iterable[str],
        prefix_pattern: str = DEFAULT_PREFIX_PATTERN,
    ) -> None:
        self.mode = mode
        self._validator = NameValidator(stoplist)
        self._pattern = re.compile(prefix_pattern)

    def resolve(self, text: str, candidate: str | None = None) -> SpeakerResult:
        """``candidate`` comes from a speaker zone or colour mask (modes ``zone``/``color``)."""
        match self.mode:
            case SpeakerMode.NONE:
                return SpeakerResult(None, text)
            case SpeakerMode.PREFIX:
                found = extract_prefix(text, self._pattern)
                if found is not None and found.rest and self._validator.is_name(found.candidate):
                    return SpeakerResult(found.candidate, found.rest)
                # rejected candidate: the prefix stays in the text ("Совет: нажмите Q")
                return SpeakerResult(None, text)
            case SpeakerMode.ZONE | SpeakerMode.COLOR:
                if candidate is not None and self._validator.is_name(candidate):
                    return SpeakerResult(candidate.strip(), text)
                return SpeakerResult(None, text)

    def resolve_all(self, text: str, candidate: str | None = None) -> list[SpeakerResult]:
        """A new line, split into the lines of several speakers when it starts with a name
        (``prefix`` mode, TZ 1.17): ``"Ви: Кто ты? Джеки: Вылезай."`` -> two results."""
        first = self.resolve(text, candidate)
        if self.mode is not SpeakerMode.PREFIX or first.name is None:
            return [first]
        parts = self.split_inner(first.text)
        return [SpeakerResult(name or first.name, part) for name, part in parts]

    def split_inner(self, text: str) -> list[tuple[str | None, str]]:
        """``(None, head)`` for the text before the first inner name (it belongs to the
        current speaker), then ``(name, text)`` per inner name; empty parts are dropped."""
        if self.mode is not SpeakerMode.PREFIX:
            return [(None, text)] if text.strip() else []
        parts: list[tuple[str | None, str]] = []
        current: str | None = None
        start = 0
        for match in INNER_NAME.finditer(text):
            name = match.group(1).strip()
            if not self._validator.is_name(name):
                continue
            parts.append((current, text[start : match.start()].strip()))
            current, start = name, match.end()
        parts.append((current, text[start:].strip()))
        return [(name, part) for name, part in parts if part]
