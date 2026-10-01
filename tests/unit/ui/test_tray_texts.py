from datetime import datetime

from subvoicer.app.facade import GameStatus
from subvoicer.core.models import HistoryEntry
from subvoicer.ui.tray import entry_caption, pretty, status_text


def test_pretty_hotkeys() -> None:
    assert pretty("<ctrl>+<alt>+r") == "Ctrl+Alt+R"
    assert pretty("<ctrl>+<shift>+<f5>") == "Ctrl+Shift+F5"


def test_status_text() -> None:
    assert status_text(GameStatus(True), ready=False).startswith("Загрузка")
    assert status_text(GameStatus(True), ready=True) == "Нет активной игры"
    game = GameStatus(True, title="Cyberpunk 2077", aspect="21:9", has_zones=False)
    assert status_text(game, ready=True) == "Cyberpunk 2077 · нет зон для 21:9"


def test_entry_caption() -> None:
    entry = HistoryEntry(1, datetime.now(), "dialog", None, "Код от двери — 4417. " * 5)
    caption = entry_caption(entry)
    assert caption.startswith("Рассказчик  Код от двери") and caption.endswith("…")
