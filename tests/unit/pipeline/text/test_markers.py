import pytest

from subvoicer.pipeline.text.markers import without_distance


@pytest.mark.parametrize(
    ("text", "clean"),
    [
        ("Поможешь? 30M", "Поможешь?"),
        ("Тебе что-то подвернулось? 0 20M", "Тебе что-то подвернулось?"),
        ("Зато ты чувствуешь себя, как дома. 10M", "Зато ты чувствуешь себя, как дома."),
        ("Придёшь на место — всё узнаешь. 20м", "Придёшь на место — всё узнаешь."),
        ("Осталось 20 м", "Осталось 20 м"),  # no end of a sentence before it
        ("Иди 20M вперёд.", "Иди 20M вперёд."),
    ],
)
def test_a_distance_marker_after_a_line_is_cut(text: str, clean: str) -> None:
    assert without_distance(text) == clean
