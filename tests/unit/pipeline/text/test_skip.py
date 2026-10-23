import pytest

from subvoicer.pipeline.text.skip import SkipList


@pytest.mark.parametrize(
    "text",
    ["[E] Взять патроны", "[Е] взять", "E Взять фальшфейер", "Влезть", "влезть!", "Влезть бы туда"],
)
def test_a_line_starting_with_a_phrase_is_skipped(text: str) -> None:
    assert SkipList(["[Е] Взять", "Влезть"]).match(text) is not None


@pytest.mark.parametrize("text", ["Влезтьё", "Он сказал: влезть", "Взять", "Нет"])
def test_other_lines_are_voiced(text: str) -> None:
    assert SkipList(["[Е] Взять", "Влезть"]).match(text) is None


def test_an_empty_list_skips_nothing() -> None:
    assert not SkipList(["", "  ", "!"])
    assert SkipList([" "]).match("Привет") is None
