"""TZ 4.4 result processing, steps 0-6 (step 1, the junk filter, runs before dedup)."""

import pytest

from subvoicer.pipeline.dedup import DedupKind, Deduplicator

LINE = "Встретимся у старого склада в полночь."


@pytest.fixture
def dedup() -> Deduplicator:
    return Deduplicator()


def test_first_line_is_new(dedup: Deduplicator) -> None:
    result = dedup.process(LINE, "zone", was_empty=True, now=0)
    assert result.kind is DedupKind.NEW
    assert result.text == LINE
    assert result.used_was_empty


def test_step0_same_text_from_another_source_within_3s(dedup: Deduplicator) -> None:
    dedup.process(LINE, "hook", was_empty=True, now=10)
    assert dedup.process(LINE, "zone", was_empty=True, now=12).kind is DedupKind.DUPLICATE


def test_step0_other_source_after_3s_is_new(dedup: Deduplicator) -> None:
    dedup.process(LINE, "hook", was_empty=True, now=10)
    assert dedup.process(LINE, "zone", was_empty=True, now=13.5).kind is DedupKind.NEW


def test_step0_same_source_is_not_a_duplicate(dedup: Deduplicator) -> None:
    dedup.process(LINE, "zone", was_empty=True, now=10)
    assert dedup.process(LINE, "zone", was_empty=True, now=11).kind is DedupKind.NEW


def test_step2_cooldown_blocks_repeat(dedup: Deduplicator) -> None:
    dedup.process(
        "Цель: найти ключ-карту охранника", "obj", was_empty=True, now=0, repeat_cooldown_min=10
    )
    again = dedup.process(
        "Цель: найти ключ-карту охранника", "obj", was_empty=True, now=300, repeat_cooldown_min=10
    )
    assert again.kind is DedupKind.RECENT
    assert not again.used_was_empty  # the flag stays for the next line


def test_step2_cooldown_expires(dedup: Deduplicator) -> None:
    dedup.process("Цель: найти ключ-карту", "obj", was_empty=True, now=0, repeat_cooldown_min=10)
    later = dedup.process(
        "Цель: найти ключ-карту", "obj", was_empty=True, now=601, repeat_cooldown_min=10
    )
    assert later.kind is DedupKind.NEW


def test_step2_cooldown_is_fuzzy(dedup: Deduplicator) -> None:
    dedup.process(
        "Цель: найти ключ-карту охранника", "obj", was_empty=True, now=0, repeat_cooldown_min=10
    )
    ocr_typo = dedup.process(
        "Цель: найти ключ-карту охранннка", "obj", was_empty=True, now=60, repeat_cooldown_min=10
    )
    assert ocr_typo.kind is DedupKind.RECENT


def test_step3_same_line_after_empty_zone_is_new_again(dedup: Deduplicator) -> None:
    dedup.process("Да.", "zone", was_empty=True, now=0)
    assert dedup.process("Да.", "zone", was_empty=True, now=5).kind is DedupKind.NEW


def test_step4_continuation_voices_only_the_tail(dedup: Deduplicator) -> None:
    dedup.process("Слушай внимательно,", "zone", was_empty=True, now=0)
    result = dedup.process("Слушай внимательно, повторять не буду.", "zone", was_empty=False, now=1)
    assert result.kind is DedupKind.CONTINUATION
    assert result.text == "повторять не буду."


def test_step4_continuation_survives_ocr_noise_in_prefix(dedup: Deduplicator) -> None:
    dedup.process("Слушай внимательно, повторять", "zone", was_empty=True, now=0)
    result = dedup.process("Слушай внимательно повторять не буду!", "zone", was_empty=False, now=1)
    assert result.kind is DedupKind.CONTINUATION
    assert result.text == "не буду!"


def test_step4_needs_more_than_3_new_chars(dedup: Deduplicator) -> None:
    dedup.process("Он уходит", "zone", was_empty=True, now=0)
    assert dedup.process("Он уходит!!", "zone", was_empty=False, now=1).kind is DedupKind.SAME
    assert dedup.process("Он уходитт", "zone", was_empty=False, now=1).kind is DedupKind.SAME


def test_step4_continuation_updates_last(dedup: Deduplicator) -> None:
    dedup.process("Раз, два,", "zone", was_empty=True, now=0)
    dedup.process("Раз, два, три, четыре,", "zone", was_empty=False, now=1)
    result = dedup.process("Раз, два, три, четыре, пять, шесть.", "zone", was_empty=False, now=2)
    assert result.kind is DedupKind.CONTINUATION
    assert result.text == "пять, шесть."


def test_step5_same_text_is_nothing(dedup: Deduplicator) -> None:
    dedup.process(LINE, "zone", was_empty=True, now=0)
    assert dedup.process(LINE, "zone", was_empty=False, now=1).kind is DedupKind.SAME


def test_step5_ocr_jitter_is_the_same_line(dedup: Deduplicator) -> None:
    dedup.process(LINE, "zone", was_empty=True, now=0)
    jitter = "Встретимся у старого скпада в полночь"
    assert dedup.process(jitter, "zone", was_empty=False, now=1).kind is DedupKind.SAME


def test_step6_different_text_is_new(dedup: Deduplicator) -> None:
    dedup.process(LINE, "zone", was_empty=True, now=0)
    result = dedup.process("Мост перекрыт, едем в объезд.", "zone", was_empty=False, now=1)
    assert result.kind is DedupKind.NEW
    assert not result.used_was_empty


def test_normalisation_ignores_case_yo_and_punctuation(dedup: Deduplicator) -> None:
    dedup.process("Ещё пять минут!", "zone", was_empty=True, now=0)
    assert dedup.process("ЕЩЕ ПЯТЬ МИНУТ", "zone", was_empty=False, now=1).kind is DedupKind.SAME


def test_channels_are_independent(dedup: Deduplicator) -> None:
    dedup.process(LINE, "a", was_empty=True, now=0)
    assert (
        dedup.process("Другая фраза целиком.", "b", was_empty=False, now=10).kind is DedupKind.NEW
    )
    assert dedup.process(LINE, "a", was_empty=False, now=11).kind is DedupKind.SAME
