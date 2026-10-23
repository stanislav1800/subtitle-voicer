"""SpeakerResolver: who says the line and what is left to voice (TZ 4.5)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from subvoicer.pipeline.speaker.extract import DEFAULT_PREFIX_PATTERN, extract_prefix
from subvoicer.pipeline.speaker.validate import NameValidator
from subvoicer.pipeline.text.latin import TWIN_HOMOGLYPHS, TWINS

# "…кто? Джеки: Вылезай." — a name after the end of a sentence starts another speaker's line
INNER_NAME = re.compile(r"(?<=[.!?…»\"])\s+([^\s:.!?…][^:.!?…]{0,29}):\s+")
# "…Скоро пройдёт. Эвелин:" — the next line's name shows up before its text (TZ 1.22)
TRAILING_NAME = re.compile(r"(?<=[.!?…»\"])\s+([^\s:.!?…][^:.!?…]{0,29}):\s*$")
# «[ П-3 ] Текст» (mode brackets, TZ 1.33): OCR may give «|» for a bracket of a thin font
BRACKET_NAME = re.compile(r"[\[|]\s*([^\[\]|]{1,40}?)\s*[\]|]")
# a name read by its colour in front of a row of the text (TZ 1.38): the zone source puts it
# into the text between two invisible separators, so every row may have a speaker of its own
NAME_MARK = "\u2063"
MARKED_NAME = re.compile(NAME_MARK + r"([^" + NAME_MARK + r"]{1,40})" + NAME_MARK)


def marked(name: str, text: str) -> str:
    """``text`` of a row whose speaker ``name`` was read by its colour."""
    return f"{NAME_MARK}{name}{NAME_MARK} {text}"


_LATIN = re.compile(r"[A-Za-z]")
_EDGES = " 	:;,.-–—=_|'\"«»"


def clean_name(candidate: str) -> str:
    """A name read from the name zone or the name colour (TZ 4.5): the colon after it and
    any text read along with it go («Пациентка: Не зн» -> «Пациентка»), Latin look-alikes in
    a Cyrillic name become Cyrillic («Bи» -> «Ви»; a Latin name like «Jackie» stays)."""
    name = candidate.split(":", 1)[0].strip(_EDGES)
    words = []
    for word in name.split():
        latin = len(_LATIN.findall(word))
        mixed = 0 < latin < len(word)  # «Bи»
        short_twins = latin == len(word) <= 3 and _all_twins(word)  # «Bu»; «Jack» stays
        if mixed or short_twins:
            word = word.translate(TWIN_HOMOGLYPHS)  # «Bu» for «Ви» in Cyberpunk's italic
        words.append(word)
    return " ".join(words)


def bracket_name(candidate: str) -> str:
    """A name in brackets: cleaned as ``clean_name``; one in capitals gets a capital letter
    only («ПРЕДСТАВИТЕЛЬ ПРЕДПРИЯТИЯ» -> «Представитель предприятия», «П-3» stays)."""
    name = clean_name(candidate)
    return name.capitalize() if name.isupper() else name


def _all_twins(word: str) -> bool:
    return all(ch in TWINS for ch in word)


class SpeakerMode(StrEnum):
    PREFIX = "prefix"
    ZONE = "zone"
    COLOR = "color"
    BRACKETS = "brackets"
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
            case SpeakerMode.BRACKETS | SpeakerMode.COLOR if self._by_marks(text):
                parts = self._marked_parts(text)
                if parts and parts[0][0] is not None:
                    return SpeakerResult(parts[0][0], parts[0][1])
                return SpeakerResult(None, self._unmarked(text))
            case SpeakerMode.BRACKETS:
                return SpeakerResult(None, text)
            case SpeakerMode.ZONE | SpeakerMode.COLOR:
                name = clean_name(candidate) if candidate is not None else ""
                if name and self._validator.is_name(name):
                    return SpeakerResult(name, text)
                return SpeakerResult(None, text)

    def resolve_all(self, text: str, candidate: str | None = None) -> list[SpeakerResult]:
        """A new line, split into the lines of several speakers when it starts with a name
        (``prefix`` mode, TZ 1.17): ``"Ви: Кто ты? Джеки: Вылезай."`` -> two results."""
        if self._by_marks(text):
            return [SpeakerResult(name, part) for name, part in self._marked_parts(text)]
        first = self.resolve(text, candidate)
        if self.mode is not SpeakerMode.PREFIX or first.name is None:
            return [first]
        parts = self.split_inner(first.text)
        return [SpeakerResult(name or first.name, part) for name, part in parts]

    def parts(self, text: str, candidate: str | None = None) -> list[SpeakerResult]:
        """Every line of a reading, for comparing readings (TZ 1.22): like ``resolve_all``,
        but a text that does not start with a name is split at inner names too."""
        if self.mode is SpeakerMode.PREFIX and self.resolve(text).name is None:
            return [SpeakerResult(name, part) for name, part in self.split_inner(text)]
        return self.resolve_all(text, candidate)

    def without_trailing_name(self, text: str) -> str:
        """``text`` without a bare name at its end, whose line has not appeared yet."""
        if self.mode in (SpeakerMode.BRACKETS, SpeakerMode.COLOR):
            names = list(self._marks().finditer(text))
            last = names[-1] if names else None
            if last is None or text[last.end() :].strip() or not self._is_mark_name(last):
                return text
            return text[: last.start()].rstrip()
        if self.mode is not SpeakerMode.PREFIX:
            return text
        match = TRAILING_NAME.search(text)
        if match is None or not self._validator.is_name(match.group(1).strip()):
            return text
        return text[: match.start()].rstrip()

    def split_inner(self, text: str) -> list[tuple[str | None, str]]:
        """``(None, head)`` for the text before the first inner name (it belongs to the
        current speaker), then ``(name, text)`` per inner name; empty parts are dropped."""
        if self._by_marks(text):
            return self._marked_parts(text)
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

    def glue_names(self, texts: list[str]) -> list[str]:
        """Plates of a «Субтитры (авто)» zone (mode brackets): a plate with only a name in it
        belongs to the plate below («[ П-3 ]» over its line)."""
        if self.mode is not SpeakerMode.BRACKETS:
            return texts
        glued: list[str] = []
        pending = ""
        for text in texts:
            if not self._marked_parts(text) and BRACKET_NAME.search(text):
                pending = f"{pending} {text}".strip()  # only names: wait for the text
                continue
            glued.append(f"{pending} {text}".strip())
            pending = ""
        return glued  # a name below the last line: its text has not appeared yet

    def _marks(self) -> re.Pattern[str]:
        """Names in the text: «[ Имя ]» (brackets), or read by the colour (marked)."""
        return BRACKET_NAME if self.mode is SpeakerMode.BRACKETS else MARKED_NAME

    def _by_marks(self, text: str) -> bool:
        if self.mode is SpeakerMode.BRACKETS:
            return True
        return self.mode is SpeakerMode.COLOR and NAME_MARK in text

    def _name_of(self, match: re.Match[str]) -> str:
        if self.mode is SpeakerMode.BRACKETS:
            return bracket_name(match.group(1))
        return clean_name(match.group(1))

    def _is_mark_name(self, match: re.Match[str]) -> bool:
        digits = self.mode is SpeakerMode.BRACKETS  # «[ П-3 ]»
        return self._validator.is_name(self._name_of(match), digits=digits)

    def _unmarked(self, text: str) -> str:
        """The text without the marks of names that did not pass (a colour read as junk)."""
        return MARKED_NAME.sub("", text).strip() if self.mode is SpeakerMode.COLOR else text

    def _marked_parts(self, text: str) -> list[tuple[str | None, str]]:
        """Every «[ Имя ]» or name read by its colour starts a line of its own (no end of a
        sentence is needed before it); the text before the first one has no name."""
        parts: list[tuple[str | None, str]] = []
        current: str | None = None
        start = 0
        for match in self._marks().finditer(text):
            if not self._is_mark_name(match):
                continue
            parts.append((current, text[start : match.start()].strip()))
            current, start = self._name_of(match), match.end()
        parts.append((current, text[start:].strip()))
        return [(name, self._unmarked(part)) for name, part in parts if self._unmarked(part)]
