"""Speaker name candidates (TZ 4.5). Stage 2: ``prefix``; ``zone``/``color`` give a candidate
from their own OCR (stage 5)."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_PREFIX_PATTERN = r"^([^:]{1,30}):\s*"


@dataclass(frozen=True, slots=True)
class PrefixMatch:
    candidate: str
    rest: str  # the text without the prefix and separator


def extract_prefix(
    text: str, pattern: str | re.Pattern[str] = DEFAULT_PREFIX_PATTERN
) -> PrefixMatch | None:
    """Name at the start of the first line, e.g. ``"Джуди: Привет"`` -> ``Джуди``."""
    regex = re.compile(pattern) if isinstance(pattern, str) else pattern
    first_line, newline, other_lines = text.partition("\n")
    match = regex.match(first_line)
    if match is None:
        return None
    rest = first_line[match.end() :] + newline + other_lines
    return PrefixMatch(match.group(1).strip(), rest.strip())
