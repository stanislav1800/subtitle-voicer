"""Numbers to words (TZ 4.7, step 3): times, ordinals with endings, percents, decimals,
integers; numbers with leading zeros (codes, phones) are read digit by digit."""

from __future__ import annotations

import re

from num2words import num2words

# ending after "45-" -> (case, gender, plural) for num2words ru ordinals
_ORDINAL_ENDINGS: dict[str, tuple[str, str, bool]] = {
    **dict.fromkeys(("й", "ый", "ий"), ("n", "m", False)),
    **dict.fromkeys(("го", "ого", "его"), ("g", "m", False)),
    **dict.fromkeys(("му", "ому", "ему"), ("d", "m", False)),
    **dict.fromkeys(("м", "ом", "ем"), ("p", "m", False)),
    **dict.fromkeys(("ым", "им"), ("i", "m", False)),
    **dict.fromkeys(("я", "ая", "ья"), ("n", "f", False)),
    **dict.fromkeys(("ю", "ую", "ью"), ("a", "f", False)),
    **dict.fromkeys(("ой", "ей"), ("g", "f", False)),
    **dict.fromkeys(("е", "ое", "ье"), ("n", "n", False)),
    **dict.fromkeys(("х", "ых", "их"), ("p", "m", True)),
    **dict.fromkeys(("ми", "ыми", "ими"), ("i", "m", True)),
}
_ENDINGS = "|".join(sorted(_ORDINAL_ENDINGS, key=len, reverse=True))

_TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_ORDINAL = re.compile(rf"(?<![\d.,])(\d+)-({_ENDINGS})(?!\w)", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s?%")
_DECIMAL = re.compile(r"(?<![\d.,])(\d+)[.,](\d+)(?![\d.,]*\d)")
_INTEGER = re.compile(r"\d+")

_DIGITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]


def cardinal(number: str) -> str:
    if len(number) > 1 and number.startswith("0"):
        return " ".join(_DIGITS[int(d)] for d in number)
    return str(num2words(int(number), lang="ru"))


def percent_word(number: str) -> str:
    if not number.isdigit():
        return "процента"
    n = int(number) % 100
    if 11 <= n <= 14:
        return "процентов"
    last = n % 10
    if last == 1:
        return "процент"
    if 2 <= last <= 4:
        return "процента"
    return "процентов"


def _decimal(whole: str, fraction: str) -> str:
    return str(num2words(float(f"{whole}.{fraction}"), lang="ru"))


def _time(m: re.Match[str]) -> str:
    hours, minutes = m.group(1), m.group(2)
    tail = "ноль ноль" if minutes == "00" else cardinal(minutes)
    return f"{cardinal(str(int(hours)))} {tail}"


def _ordinal(m: re.Match[str]) -> str:
    case, gender, plural = _ORDINAL_ENDINGS[m.group(2).lower()]
    return str(
        num2words(int(m.group(1)), lang="ru", to="ordinal", case=case, gender=gender, plural=plural)
    )


def _percent(m: re.Match[str]) -> str:
    number = m.group(1)
    if "." in number or "," in number:
        whole, fraction = re.split(r"[.,]", number)
        spoken = _decimal(whole, fraction)
    else:
        spoken = cardinal(number)
    return f"{spoken} {percent_word(number)}"


def numbers_to_words(text: str) -> str:
    text = _TIME.sub(_time, text)
    text = _ORDINAL.sub(_ordinal, text)
    text = _PERCENT.sub(_percent, text)
    text = _DECIMAL.sub(lambda m: _decimal(m.group(1), m.group(2)), text)
    return _INTEGER.sub(lambda m: cardinal(m.group(0)), text)
