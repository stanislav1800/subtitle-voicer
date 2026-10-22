"""Row merging of RapidOCR detector boxes (pure geometry, no model needed)."""

from subvoicer.engines.ocr.rapid import merge_rows


def test_overlapping_word_boxes_become_one_line() -> None:
    boxes = [(359, 44, 550, 79), (259, 42, 377, 79), (548, 44, 618, 78), (616, 48, 655, 75)]
    assert merge_rows(boxes) == [(259, 42, 655, 79)]


def test_two_text_lines_stay_separate_top_to_bottom() -> None:
    boxes = [(100, 110, 500, 150), (120, 40, 480, 80), (510, 112, 700, 148)]
    assert merge_rows(boxes) == [(120, 40, 480, 80), (100, 110, 700, 150)]


def test_far_apart_boxes_in_one_row_stay_separate() -> None:
    boxes = [(0, 10, 100, 40), (400, 10, 500, 40)]  # gap 300 > 2 * height 30
    assert merge_rows(boxes) == [(0, 10, 100, 40), (400, 10, 500, 40)]


def test_small_punctuation_box_joins_its_row() -> None:
    boxes = [(10, 20, 300, 60), (302, 45, 310, 60)]
    assert merge_rows(boxes) == [(10, 20, 310, 60)]


def test_empty() -> None:
    assert merge_rows([]) == []


def test_a_tall_icon_does_not_glue_two_lines() -> None:
    """Alan Wake: a key at the right end of a two-line plate is as tall as both lines — the
    whole plate became one «line» and was read as «Д, ндотр сисны.неиажии с»."""
    line1, icon, line2 = (20, 11, 2343, 72), (2353, 35, 2386, 122), (1005, 69, 1352, 129)
    rows = merge_rows([line1, icon, line2])
    assert line1 in rows and line2 in rows
