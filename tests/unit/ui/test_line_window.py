from subvoicer.ui.line_window import stem, suggested_entry, words


def test_words_of_a_line() -> None:
    assert words("Арасака ещё хуже, чем Милитех, пацан. Код 4417!") == [
        "Арасака",
        "ещё",
        "хуже",
        "чем",
        "Милитех",
        "пацан",
        "Код",
    ]
    assert words("По-хорошему") == ["По-хорошему"]


def test_stem_for_all_forms() -> None:
    assert stem("Арасака") == "Арасак"
    assert stem("Милитех") == "Милитех"
    assert stem("Найт-Сити") == "Найт-Сит"
    assert stem("Такэмуре") == "Такэмур"
    assert stem("ещё") == "ещё"  # too short to cut


def test_suggested_entry() -> None:
    assert suggested_entry("Арасака", True) == ("Арасак*", "Арасак*")
    assert suggested_entry("Ви", False) == ("Ви", "Ви")
