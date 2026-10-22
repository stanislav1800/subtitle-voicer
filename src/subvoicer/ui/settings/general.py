"""«Общие»: settings of TZ 4.14 that act in every game (docs/design/Main.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.facade import AppFacade
from subvoicer.config.settings import Settings
from subvoicer.ui.settings.characters import CharactersCard
from subvoicer.ui.settings.window import Page
from subvoicer.ui.tray import pretty
from subvoicer.ui.widgets.common import (
    Card,
    SliderRow,
    clear_layout,
    combo,
    label,
    row,
    stacked,
    titled,
    toggle_row,
)
from subvoicer.ui.widgets.flow import FlowLayout

APPLY_DELAY_MS = 300  # sliders: apply once the user stops dragging
POLL_RATES = (5, 10, 15, 20, 30)
OVERLAP_MODES = (
    ("speedup", "Дочитать быстрее, потом новую"),
    ("queue", "Дочитать, потом новую"),
    ("adaptive", "Ускорять по длине очереди"),
    ("cut", "Оборвать старую"),
)
PER_GAME_HINT = (
    "Это значения для всех игр. У игры могут быть свои скорость речи, режим и ускорение — "
    "в «Профилях»"
)
LIBRARY_SOURCES = (("steam", "Steam"), ("epic", "Epic Games"), ("gog", "GOG"))


class ExeList(QWidget):
    """Chips of exe names with × and a field to add one."""

    changed = Signal(list)

    def __init__(self, values: list[str]) -> None:
        super().__init__()
        self._values = list(values)
        self._chips = QWidget()
        self._flow = FlowLayout(self._chips)
        add = QHBoxLayout()
        self._field = QLineEdit()
        self._field.setObjectName("mono")
        self._field.setPlaceholderText("game.exe")
        self._field.returnPressed.connect(self._add)
        button = QPushButton("Добавить")
        button.clicked.connect(self._add)
        add.addWidget(self._field, 1)
        add.addWidget(button)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chips, 0, 0)
        layout.addLayout(add, 1, 0)
        self._render()

    def _render(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        for value in self._values:
            chip = QPushButton(f"{value}  ×")
            chip.setObjectName("segment")
            chip.setToolTip(f"Убрать {value}")
            chip.setAccessibleName(f"Убрать {value}")
            chip.clicked.connect(lambda _=False, v=value: self._remove(v))
            self._flow.addWidget(chip)
        self._chips.updateGeometry()

    def _add(self) -> None:
        value = self._field.text().strip()
        if value and value.lower() not in {v.lower() for v in self._values}:
            self._values.append(value)
            self._field.clear()
            self._render()
            self.changed.emit(list(self._values))

    def _remove(self, value: str) -> None:
        self._values = [v for v in self._values if v != value]
        self._render()
        self.changed.emit(list(self._values))


class GeneralPage(Page):
    def __init__(self, facade: AppFacade) -> None:
        super().__init__("Общие", "Действуют во всех играх. Изменения применяются сразу.")
        self._facade = facade
        self._pending: dict[str, Any] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(APPLY_DELAY_MS)
        self._timer.timeout.connect(self._apply)
        self._building = False
        self._fine_open = False  # «Тонкая настройка» shown
        self._built_from: Settings | None = None
        self._grid = QGridLayout()
        self._grid.setSpacing(16)
        self.content.addLayout(self._grid)
        # outside the grid that refresh() rebuilds: a download keeps its progress (TZ 1.24)
        self.characters = CharactersCard(
            facade, lambda on: self._set(character_voices=on, now=True)
        )
        self.content.addWidget(self.characters)
        self.finish()
        self.refresh()

    # ------------------------------------------------------------------ building

    def refresh(self) -> None:
        """Rebuilt from the current settings (they may change from the tray or a hotkey);
        the same settings as last time — only the character voices are looked at again."""
        s = self._facade.settings
        if s == self._built_from and self._grid.count():
            self.characters.refresh()
            return
        self._built_from = s
        self._building = True
        clear_layout(self._grid)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(16)
        right.setSpacing(16)
        left.addWidget(self._voicing(s))
        left.addWidget(self._queue(s))
        left.addStretch(1)
        right.addWidget(self._ducking(s))
        right.addWidget(self._system(s))
        right.addWidget(self._advanced(s))
        right.addStretch(1)
        self._grid.addLayout(left, 0, 0)
        self._grid.addLayout(right, 0, 1)
        QTimer.singleShot(0, self.fit_height)  # after the new widgets are laid out
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self._building = False
        if hasattr(self, "characters"):
            self.characters.refresh()

    def _voicing(self, s: Settings) -> Card:
        card = Card("Озвучка")
        box, switch = toggle_row(
            "Автоозвучка диалогов", f"В игре: {pretty(s.hotkeys['pause'])}", s.enabled
        )
        self._enabled = switch
        switch.toggled.connect(lambda on: self._set(enabled=on, now=True))
        card.body.addWidget(box)
        volume = SliderRow(
            "Громкость озвучки", 0, 1, 0.01, s.voice_volume, lambda v: f"{v * 100:.0f}%"
        )
        volume.changed.connect(lambda v: self._set(voice_volume=v))
        card.body.addWidget(volume)
        rate = SliderRow(
            "Базовая скорость речи", 0.5, 2.0, 0.05, s.base_rate, lambda v: f"{v:.2f}×"
        )
        rate.changed.connect(lambda v: self._set(base_rate=v))
        card.body.addWidget(rate)
        choices = self._facade.output_devices()
        devices = [("Системное по умолчанию", ""), *((shown, name) for name, shown in choices)]
        device = combo(devices, s.output_device, "Устройство вывода")
        device.currentIndexChanged.connect(
            lambda _i: self._set(output_device=device.currentData(), now=True)
        )
        card.body.addWidget(stacked(label("Устройство вывода", wrap=True), device))
        return card

    def _queue(self, s: Settings) -> Card:
        card = Card("Очередь реплик")
        overlap = combo(
            [(text, key) for key, text in OVERLAP_MODES],
            s.overlap_mode,
            "Новая реплика, пока звучит старая",
        )
        overlap.currentIndexChanged.connect(
            lambda _i: self._set(overlap_mode=overlap.currentData(), now=True)
        )
        card.body.addWidget(
            stacked(titled("Новая реплика, пока звучит старая", "того же приоритета"), overlap)
        )
        hurry = SliderRow(
            "Ускорение дочитывания", 1.0, 2.0, 0.05, s.hurry_rate, lambda v: f"{v:.2f}×"
        )
        hurry.changed.connect(lambda v: self._set(hurry_rate=v))
        card.body.addWidget(hurry)
        lag = SliderRow(
            "Реплика ждёт в очереди не дольше", 2, 30, 1, s.max_lag_s, lambda v: f"{v:.0f} с"
        )
        lag.changed.connect(lambda v: self._set(max_lag_s=v))
        card.body.addWidget(lag)
        card.body.addWidget(label(PER_GAME_HINT, "hint", wrap=True))
        return card

    def _ducking(self, s: Settings) -> Card:
        card = Card("Приглушение игры")
        level = SliderRow(
            "Громкость игры во время реплики", 0, 1, 0.05, s.duck_level, lambda v: f"{v * 100:.0f}%"
        )
        level.changed.connect(lambda v: self._set(duck_level=v))
        card.body.addWidget(level)
        card.body.addWidget(
            label(
                "Приглушается только звук игры. Исходная громкость восстанавливается "
                "при выходе и после сбоя.",
                "hint",
                wrap=True,
            )
        )
        return card

    def _system(self, s: Settings) -> Card:
        card = Card("Система")
        box, autostart = toggle_row("Запускать вместе с Windows", "", s.autostart)
        autostart.toggled.connect(lambda on: self._set(autostart=on, now=True))
        card.body.addWidget(box)
        sources = QWidget()
        sources_row = QHBoxLayout(sources)
        sources_row.setContentsMargins(0, 0, 0, 0)
        for key, name in LIBRARY_SOURCES:
            check = QCheckBox(name)
            check.setChecked(key in s.library_sources)
            check.toggled.connect(lambda on, k=key: self._toggle_source(k, on))
            sources_row.addWidget(check)
        card.body.addWidget(titled("Где искать игры", "Для списка игр в «Профилях»"))
        card.body.addWidget(sources)
        return card

    def _advanced(self, s: Settings) -> Card:
        card = Card("Дополнительно")
        box, dev = toggle_row(
            "Режим разработчика", "История с метриками, оверлей, сохранение кадров", s.dev_mode
        )
        dev.toggled.connect(lambda on: self._set(dev_mode=on, now=True))
        card.body.addWidget(box)
        if s.dev_mode:
            box, overlay = toggle_row(
                "Отладочный оверлей", "Рамки зон и метрики поверх игры", s.debug_overlay
            )
            overlay.toggled.connect(lambda on: self._set(debug_overlay=on, now=True))
            card.body.addWidget(box)
            size = QSpinBox()
            size.setRange(5, 500)
            size.setValue(s.history_size)
            size.valueChanged.connect(lambda v: self._set(history_size=v))
            size.setAccessibleName("Размер истории")
            card.body.addWidget(row(label("Размер истории"), size))
        card.body.addWidget(
            titled("Не считать играми", "Для этих программ не предлагается профиль")
        )
        excluded = ExeList(s.excluded_exes)
        excluded.changed.connect(lambda values: self._set(excluded_exes=values, now=True))
        card.body.addWidget(excluded)
        card.body.addWidget(titled("Не спрашивать", "Нажато «Не спрашивать» в уведомлении"))
        never = ExeList(s.never_ask_exes)
        never.changed.connect(lambda values: self._set(never_ask_exes=values, now=True))
        card.body.addWidget(never)
        more = QPushButton()
        more.setCheckable(True)
        card.body.addWidget(more)
        fine = self._fine(s)
        card.body.addWidget(fine)  # in the card first: a shown widget without a parent is a window
        more.toggled.connect(lambda on: self._show_fine(on, more, fine))
        more.setChecked(self._fine_open)
        self._show_fine(self._fine_open, more, fine)
        return card

    def _fine(self, s: Settings) -> QWidget:
        """Rarely changed: hidden until «Тонкая настройка» is pressed."""
        box = QWidget()
        body = QVBoxLayout(box)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        poll = combo([(f"{hz} Гц", hz) for hz in POLL_RATES], round(s.poll_hz), "Частота")
        poll.setMinimumContentsLength(6)
        poll.currentIndexChanged.connect(lambda _i: self._set(poll_hz=poll.currentData(), now=True))
        body.addWidget(
            row(
                titled(
                    "Частота проверки субтитров",
                    "Сколько раз в секунду смотреть, изменился ли текст. Новые подложки "
                    "ищутся раз в 0,3 с при любой частоте",
                ),
                poll,
            )
        )
        low = SliderRow(
            "Реплика низкого приоритета ждёт не дольше",
            1,
            20,
            1,
            s.low_priority_ttl_s,
            lambda v: f"{v:.0f} с",
        )
        low.changed.connect(lambda v: self._set(low_priority_ttl_s=v))
        body.addWidget(low)
        fade = SliderRow(
            "Плавность приглушения", 0, 1000, 10, s.duck_fade_ms, lambda v: f"{v:.0f} мс"
        )
        fade.changed.connect(lambda v: self._set(duck_fade_ms=round(v)))
        body.addWidget(fade)
        release = SliderRow(
            "Возврат громкости после фразы", 0, 2000, 50, s.duck_release_ms, lambda v: f"{v:.0f} мс"
        )
        release.changed.connect(lambda v: self._set(duck_release_ms=round(v)))
        body.addWidget(release)
        if s.dev_mode:
            cache = QSpinBox()
            cache.setRange(0, 2000)
            cache.setValue(s.tts_cache_size)
            cache.valueChanged.connect(lambda v: self._set(tts_cache_size=v))
            cache.setAccessibleName("Кеш синтеза")
            body.addWidget(row(titled("Кеш синтеза", "сколько фраз хранить готовыми"), cache))
        return box

    def _show_fine(self, on: bool, button: QPushButton, fine: QWidget) -> None:
        """Shown or hidden in place, the page is not rebuilt."""
        self._fine_open = on
        button.setText(("▾ " if on else "▸ ") + "Тонкая настройка")
        fine.setVisible(on)
        QTimer.singleShot(0, self.fit_height)

    def sync(self) -> None:
        """The tray switch or a hotkey changed «enabled» while the page is open."""
        enabled = self._facade.settings.enabled
        if self._enabled.isChecked() != enabled:
            self._building = True
            self._enabled.setChecked(enabled)
            self._building = False

    # ------------------------------------------------------------------ applying

    def _toggle_source(self, key: str, on: bool) -> None:
        sources = [k for k, _ in LIBRARY_SOURCES if k in self._facade.settings.library_sources]
        sources = [
            k for k, _ in LIBRARY_SOURCES if (k in sources and k != key) or (k == key and on)
        ]
        self._set(library_sources=sources, now=True)

    def _set(self, *, now: bool = False, **changes: Any) -> None:
        if self._building:
            return
        self._pending.update(changes)
        if now:
            self._apply()
        else:
            self._timer.start()

    def _apply(self) -> None:
        self._timer.stop()
        if not self._pending:
            return
        changes, self._pending = self._pending, {}
        rebuild = "dev_mode" in changes
        current = self._facade.settings.model_dump()
        try:
            new = Settings.model_validate({**current, **changes})
        except ValueError:
            return  # the controls keep values within range; nothing to save otherwise
        self._facade.apply_settings(new)
        if rebuild:
            QTimer.singleShot(0, self.refresh)
        else:
            self._built_from = self._facade.settings  # the controls already show it
