"""«Голоса персонажей» in «Общие» (stage 16, TZ 1.24): download and remove voices one by one,
the shared engine with the first one; the switch that runs them on the GPU."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.facade import AppFacade, CharacterRow
from subvoicer.ui.settings.dialogs import CHARACTER_MARK
from subvoicer.ui.widgets.common import Card, clear_layout, label, primary, titled, toggle_row


def size_text(size: int) -> str:
    return f"{size / 2**30:.1f} ГБ" if size >= 2**30 else f"{size / 2**20:.0f} МБ"


class _Signals(QObject):
    progress = Signal(str, float)  # text, fraction (-1: cannot be measured)
    done = Signal(str)  # error, "" — fine


class CharactersCard(Card):
    def __init__(self, facade: AppFacade, set_enabled: Callable[[bool], None]) -> None:
        super().__init__(f"Голоса персонажей {CHARACTER_MARK}")
        self._facade = facade
        self._set_enabled = set_enabled
        self._installing: str | None = None
        self._message = ""
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.done.connect(self._on_done)
        self._list = QVBoxLayout()
        self._list.setSpacing(10)
        self.body.addLayout(self._list)
        self._bar: QProgressBar | None = None
        self._status: QLabel | None = None
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self._list)
        self._bar = self._status = None
        facade = self._facade
        gpu = facade.gpu_name()
        rows = facade.character_rows()
        engine_installed, engine_size = facade.character_engine()
        usable = engine_installed and any(r.installed for r in rows)
        hint = (
            f"Тембр персонажей из игр поверх голосов ★, на видеокарте ({gpu}). Выключено — "
            "звучат голоса-основы, видеокарта не используется."
            if gpu
            else "Нужна видеокарта NVIDIA — она не найдена, голоса персонажей недоступны."
        )
        box, switch = toggle_row(
            "Использовать голоса персонажей", hint, facade.settings.character_voices and usable
        )
        switch.setEnabled(bool(gpu) and usable)
        switch.toggled.connect(self._set_enabled)
        self._list.addWidget(box)
        if not gpu:
            return
        for item in rows:
            self._list.addWidget(self._row(item, engine_installed, engine_size))
        if engine_installed:
            engine = QWidget()
            line = QHBoxLayout(engine)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(
                titled(
                    "Движок",
                    f"окружение для видеокарты, общее для всех голосов · {size_text(engine_size)}",
                ),
                1,
            )
            remove = QPushButton("Удалить")
            remove.setEnabled(self._installing is None)
            remove.clicked.connect(self._remove_engine)
            line.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)
            self._list.addWidget(engine)
        if self._message:
            self._list.addWidget(label(self._message, "hint", wrap=True))

    def _row(self, item: CharacterRow, engine_installed: bool, engine_size: int) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        line = QHBoxLayout()
        base = self._base_name(item.base)
        if item.installed:
            details = f"{item.game} · основа {base} · на диске {size_text(item.disk)}"
        elif engine_installed:
            details = (
                f"{item.game} · основа {base} · скачать {size_text(item.download)}, "
                f"на диске ~{size_text(item.disk)}"
            )
        else:  # the download is a zip or full weights: on disk it differs (TZ 1.24)
            total = size_text(item.download + engine_size)
            details = (
                f"{item.game} · основа {base} · скачать {size_text(item.download)} "
                f"(на диске ~{size_text(item.disk)}) + движок {size_text(engine_size)} "
                f"(всего {total})"
            )
        line.addWidget(titled(item.name, details), 1)
        if self._installing == item.model:
            button = QPushButton("Отмена")
            button.clicked.connect(self._facade.cancel_character_install)
        elif item.installed:
            button = QPushButton("Удалить")
            button.clicked.connect(lambda: self._remove(item))
        else:
            button = primary("Скачать", lambda: self._install(item.model))
        button.setEnabled(self._installing is None or self._installing == item.model)
        line.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(line)
        if self._installing == item.model:
            self._bar = QProgressBar()
            self._bar.setTextVisible(False)
            self._bar.setFixedHeight(8)
            self._bar.setRange(0, 0)  # busy until the first measurable step
            column.addWidget(self._bar)
            self._status = label("Готовлюсь…", "hint", wrap=True)
            column.addWidget(self._status)
        return box

    def _base_name(self, voice_id: str) -> str:
        for voice in self._facade.voices():
            if voice.id == voice_id:
                return voice.display_name
        return voice_id

    # ------------------------------------------------------------------ actions

    def _install(self, model: str) -> None:
        self._installing = model
        self._message = ""
        self.refresh()
        self._facade.install_character(
            model,
            lambda text, fraction: self._signals.progress.emit(
                text, -1.0 if fraction is None else fraction
            ),
            lambda error: self._signals.done.emit(error or ""),
        )

    def _on_progress(self, text: str, fraction: float) -> None:
        if self._bar is not None:
            if fraction < 0:
                self._bar.setRange(0, 0)
            else:
                self._bar.setRange(0, 1000)
                self._bar.setValue(round(fraction * 1000))
        if self._status is not None:
            self._status.setText(text)

    def _on_done(self, error: str) -> None:
        self._installing = None
        self._message = error
        self.refresh()

    def _remove(self, item: CharacterRow) -> None:
        users = self._facade.characters_using(item.model)
        text = f"Удалить голос «{item.name}» ({size_text(item.disk)})?"
        if users:
            text += (
                "\n\nОн назначен: " + ", ".join(users[:8]) + (" и др." if len(users) > 8 else "")
            )
            text += "\nПосле удаления они будут звучать голосом-основой."
        if (
            QMessageBox.question(self, "Удалить голос персонажа", text)
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._facade.remove_character(item.model)
        self.refresh()

    def _remove_engine(self) -> None:
        text = (
            "Удалить движок голосов персонажей? Голоса останутся, но работать не будут, "
            "пока движок не скачается снова (вместе со следующим голосом)."
        )
        if QMessageBox.question(self, "Удалить движок", text) != QMessageBox.StandardButton.Yes:
            return
        self._facade.remove_character_engine()
        self.refresh()
