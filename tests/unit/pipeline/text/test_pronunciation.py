from subvoicer.pipeline.text.pronunciation import PronunciationDict
from subvoicer.pipeline.text.pronunciation import PronunciationEntry as E


def test_plain_entry_whole_word_case_insensitive() -> None:
    d = PronunciationDict([E("NCPD", "эн си пи ди")])
    assert d.apply("В ncpd опять задержки, NCPD!") == "В эн си пи ди опять задержки, эн си пи ди!"
    assert d.apply("NCPDX") == "NCPDX"


def test_plain_entry_matches_yo_and_ye() -> None:
    d = PronunciationDict([E("Ви", "В+и")])
    assert d.apply("Ви, где ты? ВИ!") == "В+и, где ты? В+и!"
    assert d.apply("Вид") == "Вид"
    d2 = PronunciationDict([E("ёлка", "й+олка")])
    assert d2.apply("Елка и ёлка") == "й+олка и й+олка"


def test_star_entry_matches_all_forms() -> None:
    d = PronunciationDict([E("Арасак*", "Арас+ак*")])
    assert d.apply("Арасака") == "Арас+ака"
    assert d.apply("у Арасаки") == "у Арас+аки"
    assert d.apply("с Арасакой") == "с Арас+акой"
    assert d.apply("арасаке") == "Арас+аке"
    assert d.apply("Наарасака") == "Наарасака"  # only from the start of a word


def test_star_source_without_star_target_replaces_whole_word() -> None:
    d = PronunciationDict([E("Такэмур*", "Такэм+ура")])
    assert d.apply("Такэмуре") == "Такэм+ура"


def test_regex_entry() -> None:
    d = PronunciationDict([E(r"\bNC(\d+)\b", r"эн си \1", regex=True)])
    assert d.apply("NC20") == "эн си 20"


def test_order_longer_first_then_regex() -> None:
    d = PronunciationDict(
        [
            E("Джонни", "Дж+онни"),
            E("Джонни Сильверхенд", "Дж+онни Сильверх+енд"),
            E(r"Дж\+онни", "ЗАМЕНЕНО", regex=True),
        ]
    )
    # the longer plain entry wins; then the regex sees its result
    assert d.apply("Джонни Сильверхенд") == "ЗАМЕНЕНО Сильверх+енд"


def test_empty_source_is_ignored() -> None:
    assert len(PronunciationDict([E("", "x"), E("  ", "y")])) == 0
