"""First run (TZ 4.14): models are downloaded with a progress bar before anything loads.
The window closes by itself when all files are in place; on an error it offers a retry."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QWidget

from subvoicer.app.models_setup import ModelSetup, Progress
from subvoicer.core.errors import ModelsError
from subvoicer.ui import theme
from subvoicer.ui.widgets.common import label, primary

log = logging.getLogger(__name__)

BAR_STEPS = 1000


def megabytes(size: int) -> str:
    return f"{size / 2**20:.1f}".replace(".", ",")


def progress_text(progress: Progress) -> str:
    return f"{megabytes(progress.done)} из {megabytes(progress.total)} МБ"


class _Signals(QObject):
    progress = Signal(object)  # Progress
    finished = Signal(str)  # "" on success, else the error


class FirstRunWindow(QWidget):
    def __init__(self, setup: ModelSetup, on_done: Callable[[], None], on_quit: Callable[[], None]):
        super().__init__()
        self._setup = setup
        self._on_done = on_done
        self._on_quit = on_quit
        self._finished = False
        self.setObjectName("window")
        self.setWindowTitle("Subtitle Voicer — первый запуск")
        self.setWindowIcon(theme.app_icon())
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)
        layout.addWidget(label("Скачиваю модели", "windowTitle"))
        size = megabytes(sum(m.size for m in setup.missing()))
        layout.addWidget(
            label(
                f"Распознавание текста и голоса — {size} МБ, один раз. "
                "Дальше всё работает без интернета.",
                "caption",
                wrap=True,
            )
        )
        self._file = label("", "hint")
        layout.addWidget(self._file)
        self._bar = QProgressBar()
        self._bar.setRange(0, BAR_STEPS)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)
        self._amount = label("", "mono")
        layout.addWidget(self._amount)
        self._error = label("", "", wrap=True)
        self._error.setStyleSheet(f"color: {theme.CHIPS['error'].text};")
        self._error.hide()
        layout.addWidget(self._error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._quit = QPushButton("Выход")
        self._quit.clicked.connect(self.close)
        buttons.addWidget(self._quit)
        self._retry = primary("Повторить", self.start)
        self._retry.hide()
        buttons.addWidget(self._retry)
        layout.addLayout(buttons)

        self._signals = _Signals()
        self._signals.progress.connect(self._show_progress, Qt.ConnectionType.QueuedConnection)
        self._signals.finished.connect(self._done, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        self._error.hide()
        self._retry.hide()
        self._file.setText("Подключаюсь…")
        self.show()
        self.raise_()
        self.activateWindow()
        threading.Thread(target=self._run, name="Models", daemon=True).start()

    def _run(self) -> None:
        try:
            self._setup.download_missing(self._signals.progress.emit)
        except ModelsError as exc:
            log.error("Модели не скачаны: %s", exc)
            self._signals.finished.emit(str(exc))
            return
        except Exception as exc:
            log.exception("Модели не скачаны")
            self._signals.finished.emit(f"Ошибка: {exc}")
            return
        self._signals.finished.emit("")

    def _show_progress(self, progress: Progress) -> None:
        self._bar.setValue(round(progress.fraction * BAR_STEPS))
        self._amount.setText(progress_text(progress))
        if progress.title:
            self._file.setText(f"{progress.index} из {progress.count}: {progress.title}")

    def _done(self, error: str) -> None:
        if error:
            self._file.setText("Не получилось")
            self._error.setText(f"{error}\nПроверьте интернет и нажмите «Повторить».")
            self._error.show()
            self._retry.show()
            return
        self._finished = True
        self.close()
        self._on_done()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._finished:
            self._setup.cancel()  # the app cannot work without models
            self._on_quit()
        super().closeEvent(event)
