import numpy as np

from subvoicer.core.models import OcrResult
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.runner import OcrRunner, OcrSettings, reading_order
from tests.fakes.ocr import FakeOcr, line


def zone(line_height: int = 40) -> tuple[np.ndarray, np.ndarray]:
    crop = np.full((100, 300, 3), 40, dtype=np.uint8)
    mask = np.zeros((100, 300), dtype=np.uint8)
    mask[20 : 20 + line_height, 50:250] = 255
    return crop, mask


def test_confident_result_uses_default_input_only() -> None:
    ocr = FakeOcr([[line("Привет", 0.95)]])
    outcome = OcrRunner(ocr).run(*zone(), OcrInput.MASKED)
    assert outcome.text == "Привет"
    assert outcome.input_kind is OcrInput.MASKED
    assert len(ocr.inputs) == 1


def test_low_confidence_tries_other_two_and_takes_best() -> None:
    ocr = FakeOcr(
        [
            [line("Прввет", 0.5)],  # default raw
            [line("Привет", 0.9)],  # masked
            [line("Пр1вет", 0.7)],  # mask
        ]
    )
    outcome = OcrRunner(ocr).run(*zone(), OcrInput.RAW)
    assert [a.kind for a in outcome.attempts] == [OcrInput.RAW, OcrInput.MASKED, OcrInput.MASK]
    assert outcome.text == "Привет"
    assert outcome.input_kind is OcrInput.MASKED
    assert outcome.mean_confidence == 0.9


def test_low_confidence_threshold_is_mean_of_lines() -> None:
    ocr = FakeOcr([[line("а", 0.9, (0, 0, 10, 10)), line("б", 0.72, (0, 20, 10, 30))]])
    outcome = OcrRunner(ocr, OcrSettings(low_confidence=0.8)).run(*zone(), OcrInput.RAW)
    assert len(outcome.attempts) == 1  # mean 0.81


def test_empty_result_has_zero_confidence() -> None:
    ocr = FakeOcr([[], [], []])
    outcome = OcrRunner(ocr).run(*zone(), OcrInput.RAW)
    assert outcome.text == ""
    assert outcome.mean_confidence == 0.0
    assert len(outcome.attempts) == 3


def test_lines_joined_in_reading_order() -> None:
    ocr = FakeOcr(
        [
            [
                line("строка,", 0.9, (10, 60, 200, 90)),
                line("вторая", 0.9, (210, 62, 300, 88)),
                line("Первая", 0.9, (10, 10, 200, 40)),
            ]
        ]
    )
    runner = OcrRunner(ocr)
    assert runner.run(*zone(), OcrInput.RAW).text == "Первая строка, вторая"


def test_newline_separator_for_manual_reading() -> None:
    ocr = FakeOcr([[line("Раз", 0.9, (0, 0, 50, 20)), line("Два", 0.9, (0, 40, 50, 60))]])
    outcome = OcrRunner(ocr).run(*zone(), OcrInput.RAW, separator="\n")
    assert outcome.text == "Раз\nДва"


def test_bboxes_are_mapped_back_after_upscale() -> None:
    ocr = FakeOcr([[line("Текст", 0.9, (100, 40, 500, 80))]])
    outcome = OcrRunner(ocr, OcrSettings(upscale=2.0)).run(*zone(line_height=12), OcrInput.RAW)
    assert ocr.inputs[0].shape[:2] == (200, 600)
    assert outcome.best.lines[0].bbox == (50, 20, 250, 40)


def test_reading_order_groups_rows_by_vertical_overlap() -> None:
    result = OcrResult(
        (
            line("C", bbox=(0, 50, 10, 70)),
            line("B", bbox=(100, 2, 110, 22)),
            line("A", bbox=(0, 0, 10, 20)),
            line(" ", bbox=(0, 90, 10, 99)),
        )
    )
    assert [ln.text for ln in reading_order(result)] == ["A", "B", "C"]


def test_a_fallback_with_worse_words_does_not_win() -> None:
    """Cyberpunk letsplay: the mask input lost thin letters, yet the engine was sure of it."""
    from subvoicer.pipeline.ocr.words import WordFixer

    words = WordFixer({"да": 900_000, "я": 5_000_000, "подскажу": 900, "слушай": 9_000})
    ocr = FakeOcr(
        [
            [line("Да я подскажу", 0.75)],  # default raw
            [line("Да я оо кудозною", 0.93)],  # masked
            [line("Да я подскажу", 0.78)],  # mask
        ]
    )
    outcome = OcrRunner(ocr, judge=words.known_share).run(*zone(), OcrInput.RAW)
    assert outcome.text == "Да я подскажу" and outcome.mean_confidence == 0.78
    # an empty or junk default still loses to a confident reading
    ocr = FakeOcr([[line("-", 0.2)], [line("Слушай", 0.9)], [line("Слушай", 0.8)]])
    outcome = OcrRunner(ocr, judge=words.known_share).run(*zone(), OcrInput.RAW)
    assert outcome.text == "Слушай" and outcome.input_kind is OcrInput.MASKED


def test_known_share_counts_only_common_short_words() -> None:
    from subvoicer.pipeline.ocr.words import WordFixer

    words = WordFixer({"спо": 19, "ибо": 3_144, "ну": 400_000, "э": 14_000}, known=["Ви"])
    assert words.known_share("Ну Ви") == 1.0
    assert words.known_share("спо ибо") == 0.5  # «спо» is a fragment, «ибо» a word
    assert words.known_share("э") == 0.0
    assert words.known_share("…") == 1.0
