from datetime import datetime

from subvoicer.core.models import HistoryEntry
from subvoicer.ui.settings.history import matches, metrics, outcome_chip


def entry(**fields: object) -> HistoryEntry:
    base: dict[str, object] = {
        "id": 1,
        "time": datetime(2026, 10, 1, 21, 14, 8),
        "channel": "dialog",
        "speaker": "Джуди",
        "text": "Ви, ты вообще меня слушаешь?",
    }
    base.update(fields)
    return HistoryEntry(**base)  # type: ignore[arg-type]


def test_outcomes() -> None:
    assert outcome_chip("done") == ("дозвучала", "success")
    assert outcome_chip("cut") == ("оборвана", "warning")
    assert outcome_chip("dropped:max_lag") == ("отброшена · max_lag", "error")


def test_metrics_and_search() -> None:
    text = metrics(
        entry(confidence=0.94, ocr_input="masked", latency_ms=560.4, rate=1.15, priority="high")
    )
    assert text == "OCR 0.94 · вход (б)\nзвук 560 мс ×1.15\nhigh"
    assert matches(entry(zone_name="Диалоги"), "диалог")
    assert not matches(entry(), "Панам")
