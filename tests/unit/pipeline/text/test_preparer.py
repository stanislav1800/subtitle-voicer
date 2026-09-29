from subvoicer.config.resources import letter_names, translit_table
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.preparer import TextPreparer
from subvoicer.pipeline.text.pronunciation import PronunciationDict
from subvoicer.pipeline.text.pronunciation import PronunciationEntry as E
from subvoicer.pipeline.text.stress import add_stress
from tests.fakes.stress import FirstVowelStress, OverwritingStress


def preparer(dictionary: PronunciationDict | None = None) -> TextPreparer:
    return TextPreparer(
        FirstVowelStress(), LatinReader(letter_names(), translit_table()), dictionary
    )


def test_full_order() -> None:
    d = PronunciationDict([E("NCPD", "эн си пи д+и"), E("Арасак*", "Арас+ак*")])
    (piece,) = preparer(d).prepare("NCPD и Арасаки: 3 GPS...")
    # dictionary first (NCPD not read as letters), numbers, latin, stress, cleanup
    assert piece == "+эн с+и п+и д+и +и Арас+аки: тр+и дж+и п+и +эс"


def test_dictionary_stress_survives_the_marker() -> None:
    (piece,) = preparer(PronunciationDict([E("замок", "зам+ок")])).prepare("Это замок")
    assert piece == "+Это зам+ок"


def test_long_text_split_into_pieces() -> None:
    pieces = preparer().prepare("Раз. Два. Три.")
    assert pieces == ["Р+аз.", "Дв+а.", "Тр+и."]


def test_per_call_dictionary_overrides_default() -> None:
    p = preparer(PronunciationDict([E("Ви", "В+и")]))
    assert p.prepare("Ви", PronunciationDict([E("Ви", "Виии")])) == ["В+иии"]


def test_guard_against_marker_overwriting_manual_stress() -> None:
    marker = OverwritingStress()
    assert add_stress("Это зам+ок и дом", marker) == "+Это зам+ок +и д+ом"


def test_empty_text() -> None:
    assert preparer().prepare("") == []
