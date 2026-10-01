"""«История» — developer mode only (TZ 4.13.2, docs/design/History.html)."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.facade import AppFacade
from subvoicer.core.models import HistoryEntry
from subvoicer.ui.settings.dialogs import voice_text
from subvoicer.ui.settings.window import Page
from subvoicer.ui.widgets.common import chip, label

REFRESH_MS = 1000
OUTCOMES = {
    "queued": ("в очереди", "info"),
    "playing": ("звучит", "info"),
    "done": ("дозвучала", "success"),
    "cut": ("оборвана", "warning"),
}
INPUTS = {"raw": "вход (а)", "masked": "вход (б)", "mask": "вход (в)"}


def outcome_chip(outcome: str) -> tuple[str, str]:
    if outcome.startswith("dropped"):
        reason = outcome.partition(":")[2]
        return (f"отброшена · {reason}" if reason else "отброшена"), "error"
    return OUTCOMES.get(outcome, (outcome, "info"))


def metrics(entry: HistoryEntry) -> str:
    ocr = f"OCR {entry.confidence:.2f} · {INPUTS.get(entry.ocr_input, entry.ocr_input or '—')}"
    sound = f"звук {entry.latency_ms:.0f} мс" if entry.latency_ms is not None else "звук —"
    rate = f"×{entry.rate:.2f}" if entry.rate != 1.0 else ""
    return "\n".join(p for p in (ocr, f"{sound} {rate}".strip(), entry.priority or "") if p)


def matches(entry: HistoryEntry, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            entry.text,
            entry.speaker or "",
            entry.zone_name,
            entry.channel,
            entry.prepared,
            entry.voice,
        ]
    ).lower()
    return query.lower() in haystack


class HistoryPage(Page):
    def __init__(self, facade: AppFacade) -> None:
        super().__init__("История", "Последние реплики с метриками распознавания и озвучки")
        self._facade = facade
        head = QHBoxLayout()
        head.addWidget(chip("режим разработчика", "warning"))
        head.addStretch(1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Поиск по истории")
        self._search.textChanged.connect(lambda _t: self.refresh())
        head.addWidget(self._search, 1)
        clear = QPushButton("Очистить")
        clear.clicked.connect(self._clear)
        head.addWidget(clear)
        self.content.addLayout(head)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["ВРЕМЯ", "КТО", "ТЕКСТ", "МЕТРИКИ", "ИТОГ"])
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        for column, width in ((0, 76), (1, 110), (3, 150), (4, 120)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, width)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.content.addWidget(self._table, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._shown_ids: tuple[tuple[int, str], ...] = ()

    def refresh(self) -> None:
        self._timer.start()
        self._fill(force=True)

    def _tick(self) -> None:
        if not self.isVisible():
            self._timer.stop()
            return
        self._fill(force=False)

    def _clear(self) -> None:
        self._facade.history.clear()
        self._fill(force=True)

    def _fill(self, *, force: bool) -> None:
        query = self._search.text().strip()
        entries = [e for e in self._facade.recent() if matches(e, query)]
        key = tuple((e.id, e.outcome) for e in entries)
        if not force and key == self._shown_ids:
            return
        self._shown_ids = key
        voices = self._facade.voices()
        table = self._table
        table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            table.setCellWidget(row, 0, label(f" {entry.time:%H:%M:%S}", "mono"))
            who = QWidget()
            who_box = QHBoxLayout(who)
            who_box.setContentsMargins(4, 0, 4, 0)
            who_box.addWidget(chip(entry.speaker or "—", "import" if entry.speaker else "info"))
            who_box.addStretch(1)
            table.setCellWidget(row, 1, who)
            text = QWidget()
            text_box = QVBoxLayout(text)
            text_box.setContentsMargins(6, 4, 6, 4)
            text_box.setSpacing(1)
            text_box.addWidget(label(entry.text, "", wrap=True))
            voice = voice_text(entry.voice, voices) if entry.voice else "—"
            details = " · ".join(
                p for p in (entry.zone_name or entry.channel, voice, entry.voice_reason) if p
            )
            text_box.addWidget(label(details, "hint", wrap=True))
            if entry.prepared and entry.prepared != entry.text:
                prepared = label(f"→ {entry.prepared}", "mono", wrap=True)
                text_box.addWidget(prepared)
            table.setCellWidget(row, 2, text)
            table.setCellWidget(row, 3, label(metrics(entry), "mono"))
            outcome_box = QWidget()
            outcome_layout = QHBoxLayout(outcome_box)
            outcome_layout.setContentsMargins(4, 0, 4, 0)
            text_, kind = outcome_chip(entry.outcome)
            outcome_layout.addWidget(chip(text_, kind))
            outcome_layout.addStretch(1)
            table.setCellWidget(row, 4, outcome_box)
        for row in range(table.rowCount()):
            cells = [table.cellWidget(row, column) for column in range(table.columnCount())]
            width = table.columnWidth(2)
            heights = [
                c.heightForWidth(width) if c.hasHeightForWidth() else c.sizeHint().height()
                for c in cells
                if c is not None
            ]
            table.setRowHeight(row, max([48, *heights]) + 8)
        height = table.horizontalHeader().height() + sum(
            table.rowHeight(r) for r in range(table.rowCount())
        )
        table.setMinimumHeight(min(max(height + 4, 120), 2000))
        self.fit_height()
