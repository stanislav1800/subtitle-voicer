from subvoicer.pipeline.normalize import normalize, normalize_with_map, raw_tail


def test_normalize() -> None:
    assert normalize("  Ещё — ПЯТЬ минут!!  Ну... ") == "еще пять минут ну"


def test_map_points_into_original() -> None:
    text = "Да, ещё!"
    norm, index = normalize_with_map(text)
    assert norm == "да еще"
    assert [text[i] for i in index] == ["Д", "а", ",", "е", "щ", "ё"]


def test_raw_tail_drops_leading_separators() -> None:
    assert raw_tail("Слушай, повторять не буду.", len("слушай")) == "повторять не буду."
    assert raw_tail("Раз, два... три!", len("раз два")) == "три!"
    assert raw_tail("Раз, два, три!", len("раз ")) == "два, три!"


def test_raw_tail_edges() -> None:
    assert raw_tail("Раз два", 0) == "Раз два"
    assert raw_tail("Раз два", 100) == ""
