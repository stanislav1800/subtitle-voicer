"""«Голоса персонажей» in «Общие» (stage 16, TZ 1.24, 1.25): download and remove voices one by
one, the shared engine with the first one — for the NVIDIA GPU or the CPU, the GPUs Windows
found are shown; the switch that turns them on."""

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

from subvoicer.app.character_setup import CPU, CUDA, ROCM
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
        hardware = facade.character_hardware()
        rows = facade.character_rows()
        engine_installed, engine_size, built = facade.character_engine()
        usable = engine_installed and any(r.installed for r in rows)
        device = built if engine_installed and built else facade.character_device
        if device in (CUDA, ROCM):
            gpu = hardware.nvidia if device == CUDA else hardware.radeon
            hint = (
                f"Тембр персонажей из игр поверх голосов ★, на видеокарте "
                f"({gpu or 'видеокарта'}). Выключено — звучат голоса-основы, "
                "видеокарта не используется."
            )
        else:
            hint = (
                "Тембр персонажей из игр поверх голосов ★, на процессоре. "
                "Выключено — звучат голоса-основы."
            )
        box, switch = toggle_row(
            "Использовать голоса персонажей", hint, facade.settings.character_voices and usable
        )
        switch.setEnabled(usable)
        switch.toggled.connect(self._set_enabled)
        self._list.addWidget(box)
        if not engine_installed:
            self._list.addWidget(self._device_row(hardware, device))
        for item in rows:
            self._list.addWidget(self._row(item, engine_installed, engine_size))
        if engine_installed:
            engine = QWidget()
            line = QHBoxLayout(engine)
            line.setContentsMargins(0, 0, 0, 0)
            target = "процессора" if device == CPU else "видеокарты"
            line.addWidget(
                titled(
                    "Движок",
                    f"окружение для {target}, общее для всех голосов · {size_text(engine_size)}",
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
