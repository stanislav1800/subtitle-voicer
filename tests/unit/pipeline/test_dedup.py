"""TZ 4.4 result processing, steps 0-6 (step 1, the junk filter, runs before dedup)."""

import pytest

from subvoicer.pipeline.dedup import DedupKind, Deduplicator, continues, on_screen

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


def test_the_same_long_line_read_a_few_letters_longer_is_not_a_continuation() -> None:
    """TZ 1.22: OCR splits a glued word ("потерялсвязь" -> "потерял связь"), the text grows by
    a few letters, and its "tail" is just the end of the line again."""
    d = Deduplicator()
    first = "Ёринобу: Сабуро потерялсвязь срельностью, живёт вмире, которого нет! Может, и не было!"
    again = (
        "Ёринобу: Сабуро потерял связь с реальностью, живёт в мире, которого нет! Может, и не было!"
    )
    assert d.process(first, "z", was_empty=True, now=0).kind is DedupKind.NEW
    assert d.process(again, "z", was_empty=False, now=1).kind is DedupKind.SAME


def test_on_screen_lines() -> None:
    from subvoicer.pipeline.dedup import on_screen

    shown = ["Располагайся пока, я закончу через минуту.", "Может, никогда и не было!"]
    assert on_screen("Располагайся пока, я зокончу через минуту.", shown)  # OCR noise
    assert on_screen("пока, я закончу через минуту.", shown)  # the top cut
    assert on_screen("Так послушай ещё. Может, никогда и не было!", shown)  # top revealed
    assert not on_screen("Надо послушать их разговор.", shown)
    assert not on_screen("и не было", shown)  # too short to tell


@pytest.mark.parametrize(
    ("worse", "line"),
    [
        (
            "Администратор Уоико о Здрав вуи е и добропожалова ьв Комп кипло о",
            "Администратор: Уоикоо. Здравствуйте и добро пожаловать в «Компэки плаза».",
        ),
        ("Ви Мы о е бы оре и ирова ься", "Ви: Мы хотели бы зарегистрироваться."),
        (
            "н о норма мо иров о о а а в яж",
            "Ну что, нормальная маскировка? Ха-ха-ха-хо-х.. Как я выгляжу?",
        ),
        ("пер Джеки Бо о ов", "Супер. Джеки, «Болт» готов?"),
        (
            "Мненрови я Хорошии вьбор Бо Дожежолко ч оном еича у",
            "Мне нравится. Хороший выбор, Бог. Джжлко, что нам сейчас не",
        ),
    ],
)
def test_a_worse_reading_of_a_line_on_screen_is_not_new(worse: str, line: str) -> None:
    """TZ 1.27: the background behind a still subtitle flickers, the zone is read again and
    OCR loses letters (from a let's play log and recording)."""
    assert on_screen(worse, [line])


@pytest.mark.parametrize(
    ("new", "line"),
    [
        ("Ви: Именно так.", "Администратор: Комната с двумя кроватями, на одну ночь. Верно?"),
        ("Джеки: Да, да, я помню.", "Ти-Баг: Я не для тебя выбирала. Отсюда ближе к Арасаке."),
        (
            "Ти-Баг: Простота требует большого мастерства.",
            "Ти-Баг: Запускайте «Болта» и ищите вход в вентиляцию.",
        ),
        (
            "Администратор: Замечательно. Позвольте я уведомлю, что вы приехали",
            "Администратор: Комната с двумя кроватями на одну ночь. Верно?",
        ),
    ],
)
def test_another_line_is_not_a_worse_reading(new: str, line: str) -> None:
    assert not on_screen(new, [line])


def test_a_better_reading_after_a_worse_one_is_not_dropped() -> None:
    """The first reading was the worse one: the clean line that follows is no «worse copy»."""
    assert not on_screen(
        "Ви: Мы немного придём в себя и сами его уведомим.",
        ["Ви Мы немно ридем в ебя и оми е о уведомим"],
    )


@pytest.mark.parametrize(
    ("previous", "text", "expected"),
    [
        ("А теперь это происходило на самом деле, и я не", "мог выдавить ни строчки.", True),
        ("Все... прямо как на той странице...", "которую я нашел.", True),
        ("…и снижаете уровень", "холестерина в крови.", True),
        ("Мост перекрыт.", "едем в объезд", False),
        ("Что ты делаешь?", "ничего", False),
        ("Мост перекрыт, и", "Мы едем в объезд.", False),
        ("Мост перекрыт, и", "", False),
        ("«Я не знаю»", "сказал он.", True),
    ],
)
def test_continues(previous: str, text: str, expected: bool) -> None:
    assert continues(previous, text) is expected


def test_a_worse_reading_of_the_same_subtitle_is_not_a_new_line() -> None:
    """TZ 1.37: the zone never went empty, a blurry frame read the line otherwise."""
    d = Deduplicator()
    first = "Ладно, я скажу тебе, почему я хочу разрушить «Арасаку», но скажу только"
    assert d.process(first, "z", was_empty=True, now=0).kind is DedupKind.NEW
    again = "Лодняскжутебе,почемуя хочу разрушить «Араса, но скажу только"
    assert d.process(again, "z", was_empty=False, now=1).kind is DedupKind.SAME
    assert d.same_as_last("Ладно,яскжутебе,почемухчуразушить «Арасаку»,но скажу только", "z")
    # a short line keeps the strict match, a line added below is new
    d.process("Ты кто?", "z", was_empty=True, now=2)
    assert d.process("Ты что?", "z", was_empty=False, now=3).kind is DedupKind.NEW
    d.process("Расслабься, все под контролем.", "z", was_empty=True, now=4)
    added = "Расслабься, все под контролем. Джуди: Я вижу ходячие говорящие трупы."
    assert d.process(added, "z", was_empty=False, now=5).kind is not DedupKind.SAME
