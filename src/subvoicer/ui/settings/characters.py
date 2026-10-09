"""«Голоса персонажей» in «Общие» (stage 16, TZ 1.24, 1.25): the switch that turns them on; the
engine first — where it works (NVIDIA, Radeon or the CPU, from the GPUs Windows found), install,
cancel, remove; then the voices one by one, locked until the engine is there."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.character_setup import CPU, CUDA, ENGINE, ROCM
from subvoicer.app.facade import AppFacade, CharacterHardware, CharacterRow
from subvoicer.ui.settings.dialogs import CHARACTER_MARK
from subvoicer.ui.widgets.common import (
    Card,
    clear_layout,
    combo,
    label,
    primary,
    titled,
    toggle_row,
)

CPU_NOTE = "медленно: +1–6 с к реплике"  # measured, DECISIONS.md (TZ 1.25)


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
        self._installing: str | None = None  # ENGINE or a voice model
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
        hardware = facade.character_hardware()
        rows = facade.character_rows()
        engine_installed, engine_size, built = facade.character_engine()
        usable = engine_installed and any(r.installed for r in rows)
        device = built if engine_installed and built else facade.character_device
        if device == CPU:
            hint = (
                "Тембр персонажей из игр поверх голосов ★, на процессоре. "
                "Выключено — звучат голоса-основы."
            )
        else:
            hint = (
                f"Тембр персонажей из игр поверх голосов ★, на видеокарте "
                f"({self._gpu(hardware, device)}). Выключено — звучат голоса-основы, "
                "видеокарта не используется."
            )
        box, switch = toggle_row(
            "Использовать голоса персонажей", hint, facade.settings.character_voices and usable
        )
        switch.setEnabled(usable)
        switch.toggled.connect(self._set_enabled)
        self._list.addWidget(box)
        if not engine_installed and self._installing != ENGINE:
            self._list.addWidget(self._device_row(hardware, device))
        self._list.addWidget(self._engine_row(hardware, engine_installed, engine_size, device))
        best = hardware.devices()[0]
        if engine_installed and device == CPU and best != CPU:
            self._list.addWidget(
                label(
                    f"Найдена видеокарта {self._gpu(hardware, best)}. Чтобы голоса работали "
                    "на ней, удалите движок и установите его заново.",
                    "hint",
                    wrap=True,
                )
            )
        if not engine_installed:
            self._list.addWidget(
                label("Установите движок, чтобы скачивать голоса персонажей.", "hint", wrap=True)
            )
        for item in rows:
            self._list.addWidget(self._row(item, engine_installed))
        if self._message:
            self._list.addWidget(label(self._message, "hint", wrap=True))

    @staticmethod
    def _gpu(hardware: CharacterHardware, device: str) -> str:
        if device == CUDA:
            return hardware.nvidia or "NVIDIA"
        return hardware.radeon or "Radeon"

    def _device_row(self, hardware: CharacterHardware, device: str) -> QWidget:
        """Where the engine will work: chosen from the list if a GPU fits, else the CPU with
        what Windows found and why it does not fit (TZ 1.25)."""
        found = ", ".join(hardware.others)
        unfit = (
            f"{found} — для {'них' if len(hardware.others) > 1 else 'неё'} движка нет: нужна "
            "NVIDIA или Radeon RX 7700 и новее на Windows 11"
            if found
            else ""
        )
        devices = hardware.devices()
        if devices == [CPU]:
            text = f"Найдено: {unfit}." if unfit else "Видеокарты не найдены."
            return label(
                f"{text} Голоса персонажей будут работать на процессоре, {CPU_NOTE}.",
                "hint",
                wrap=True,
            )
        box = QWidget()
        line = QHBoxLayout(box)
        line.setContentsMargins(0, 0, 0, 0)
        gpus = [g for g in (hardware.nvidia, hardware.radeon) if g]
        hint = (
            f"Видеокарта {', '.join(gpus)} — быстрее. Процессор — меньше места на диске, "
            f"но {CPU_NOTE}"
        )
        if hardware.radeon and not hardware.nvidia:
            hint += ". Radeon — предварительная поддержка AMD, нужен свежий драйвер Adrenalin"
        if unfit:
            hint += f". Ещё найдено: {unfit}"
        line.addWidget(titled("Где работать", hint), 1)
        names = {CUDA: "Видеокарта NVIDIA", ROCM: "Видеокарта Radeon", CPU: "Процессор"}
        choice = combo([(names[d], d) for d in devices], device, "Где работать")
        choice.setEnabled(self._installing is None)
        choice.currentIndexChanged.connect(lambda _i: self._choose(str(choice.currentData())))
        line.addWidget(choice, 0, Qt.AlignmentFlag.AlignVCenter)
        return box

    def _choose(self, device: str) -> None:
        self._facade.character_device = device
        QTimer.singleShot(0, self.refresh)  # not from inside the signal of the combo itself

    def _engine_row(
        self, hardware: CharacterHardware, installed: bool, size: int, device: str
    ) -> QWidget:
        target = "процессора" if device == CPU else f"видеокарты ({self._gpu(hardware, device)})"
        size_note = f"на диске {size_text(size)}" if installed else f"~{size_text(size)}"
        details = f"для {target}, общий для всех голосов · {size_note}"
        if self._installing == ENGINE:
            button = QPushButton("Отмена")
            button.clicked.connect(self._facade.cancel_character_install)
        elif installed:
            button = QPushButton("Удалить")
            button.clicked.connect(self._remove_engine)
            button.setEnabled(self._installing is None)
        else:
            button = primary("Установить движок", self._install_engine)
            button.setEnabled(self._installing is None)
        return self._line("Движок", details, button, self._installing == ENGINE)

    def _row(self, item: CharacterRow, engine_installed: bool) -> QWidget:
        base = self._base_name(item.base)
        if item.installed:
            details = f"{item.game} · основа {base} · на диске {size_text(item.disk)}"
        else:  # the download is a zip or full weights: on disk it differs (TZ 1.24)
            details = (
                f"{item.game} · основа {base} · скачать {size_text(item.download)}, "
                f"на диске ~{size_text(item.disk)}"
            )
        if self._installing == item.model:
            button = QPushButton("Отмена")
            button.clicked.connect(self._facade.cancel_character_install)
        elif item.installed:  # removing needs no engine
            button = QPushButton("Удалить")
            button.clicked.connect(lambda: self._remove(item))
            button.setEnabled(self._installing is None)
        else:
            button = primary("Скачать", lambda: self._install(item.model))
            button.setEnabled(self._installing is None and engine_installed)
        box = self._line(item.name, details, button, self._installing == item.model)
        box.setEnabled(engine_installed or item.installed)  # grey until the engine is there
        return box

    def _line(self, title: str, details: str, button: QPushButton, busy: bool) -> QWidget:
        """A row: title and details, the button on the right; the progress under it if busy."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        line = QHBoxLayout()
        line.addWidget(titled(title, details), 1)
        line.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(line)
        if busy:
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

    def _install_engine(self) -> None:
        self._start(ENGINE)
        self._facade.install_character_engine(*self._callbacks())

    def _install(self, model: str) -> None:
        self._start(model)
        self._facade.install_character(model, *self._callbacks())

    def _start(self, what: str) -> None:
        self._installing = what
        self._message = ""
        self.refresh()

    def _callbacks(
        self,
    ) -> tuple[Callable[[str, float | None], None], Callable[[str | None], None]]:
        return (
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
            "Удалить движок голосов персонажей? Скачанные голоса останутся на диске, но "
            "работать не будут, пока движок не установлен снова — тогда можно выбрать, "
            "где ему работать."
        )
        if QMessageBox.question(self, "Удалить движок", text) != QMessageBox.StandardButton.Yes:
            return
        self._facade.remove_character_engine()
        self.refresh()
