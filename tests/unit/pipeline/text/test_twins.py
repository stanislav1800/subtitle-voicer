"""Russian read with Latin twins becomes Cyrillic before the junk filter (short lines)."""

import pytest

from subvoicer.pipeline.text.latin import twins_to_cyrillic


@pytest.mark.parametrize(
    ("read", "fixed"),
    [
        ("HeT", "Нет"),  # was dropped as not Cyrillic: short lines went unvoiced
        ("Y-y! Ara!", "У-у! Ага!"),
        ("Xa-xa-xa!", "Ха-ха-ха!"),
        ("Tак точно", "Так точно"),  # a word mixing the scripts
        ("REMEDY", "REMEDY"),  # real Latin stays and is still dropped
        ("«Children of the elder god»", "«Children of the elder god»"),
        ("Привет, Jackie!", "Привет, Jackie!"),
        ("Получено 50 HP", "Получено 50 HP"),  # an abbreviation in a Russian line
    ],
)
def test_twins(read: str, fixed: str) -> None:
    assert twins_to_cyrillic(read) == fixed
