"""SpeakerResolver: who says the line and what is left to voice (TZ 4.5)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from subvoicer.pipeline.speaker.extract import DEFAULT_PREFIX_PATTERN, extract_prefix
from subvoicer.pipeline.speaker.validate import NameValidator


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
