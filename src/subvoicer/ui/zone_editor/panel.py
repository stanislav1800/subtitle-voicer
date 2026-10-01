"""Right panel of the zone editor: zone list, templates, properties, live preview (TZ 4.13)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.zone_tools import ZonePreview, as_rgb_bytes
from subvoicer.core.models import Voice
from subvoicer.ui import theme
from subvoicer.ui.settings.dialogs import voice_items, voice_text
from subvoicer.ui.settings.hotkeys import CaptureDialog
from subvoicer.ui.tray import pretty
from subvoicer.ui.widgets.common import (
    Segmented,
    SliderRow,
    clear_layout,
    combo,
    icon_button,
    label,
    toggle_row,
)

PANEL_WIDTH = 400
TEMPLATE_TITLES = {
    "dialog": "Диалоги",
    "note": "Записка",
    "notification": "Уведомления",
    "objective": "Цель задания",
    "speaker": "Имя говорящего",
}
PRIORITY = (("high", "Высокий"), ("normal", "Обычн."), ("low", "Низкий"))
TRIGGER = (("auto", "Авто"), ("hotkey", "Клавиша"), ("off", "Выкл"))
VOICE = (("speaker", "По говорящему"), ("narrator", "Рассказчик"), ("fixed", "Выбранный"))
SPEAKER = (
    ("color", "По цвету"),
    ("prefix", "Перед текстом"),
    ("zone", "Зона"),
    ("none", "Нет"),
)
COOLDOWN = ((0.0, "нет"), (2.0, "2 мин"), (10.0, "10 мин"))
PRESET = (
    ("color", "По цвету"),
    ("bright_on_dark", "Светлый на тёмном"),
    ("dark_on_bright", "Тёмный на светлом"),
    ("adaptive", "Адаптивный"),
)
DEFAULT_THRESHOLDS = {"bright_on_dark": 200, "dark_on_bright": 60}


def zone_color(index: int) -> str:
    return theme.ZONE_COLORS[index % len(theme.ZONE_COLORS)]


def swatch(ranges: list[Any]) -> QLabel:
    """The centre colour of the first HSV range (OpenCV H 0-179 -> Qt 0-359)."""
    result = QLabel()
    result.setFixedSize(28, 28)
    if ranges:
        (h1, s1, v1), (h2, s2, v2) = ranges[0]
        color = QColor.fromHsv(int((h1 + h2) / 2 * 2) % 360, int((s1 + s2) / 2), int((v1 + v2) / 2))
        result.setStyleSheet(
            f"background: {color.name()}; border-radius: 6px;"
            f" border: 1px solid {theme.BORDER_CONTROL};"
        )
    else:
        result.setStyleSheet(f"border: 1px dashed {theme.BORDER_CONTROL}; border-radius: 6px;")
    return result


def section(title: str) -> QLabel:
    return label(title.upper(), "sectionTitle")


class ZonePanel(QScrollArea):
    template_chosen = Signal(str)
    zone_selected = Signal(str)
    zone_changed = Signal(dict)  # the whole zone as a dict (Zone fields)
    zone_deleted = Signal(str)
    pipette = Signal(str)  # "text" | "name": the next click on the frame picks a colour
    listen = Signal()

    def __init__(self, voices: list[Voice], taken_hotkeys: Callable[[str], dict[str, str]]) -> None:
        super().__init__()
        self.setFixedWidth(PANEL_WIDTH)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"QScrollArea {{ background: {theme.BG_WINDOW};"
            f" border-left: 1px solid {theme.DIVIDER}; }}"
        )
        self._voices = voices
        self._taken = taken_hotkeys
        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(18, 16, 26, 16)
        self._layout.setSpacing(10)
        head = QHBoxLayout()
        self._title = section("Зоны")
        head.addWidget(self._title, 1)
        templates = QPushButton("  Из шаблона")
        templates.setIcon(theme.icon("plus"))
        menu = QMenu(templates)
        for key, title in TEMPLATE_TITLES.items():
            menu.addAction(title).triggered.connect(
                lambda _=False, k=key: self.template_chosen.emit(k)
            )
        templates.setMenu(menu)
        head.addWidget(templates)
        self._layout.addLayout(head)
        self._list = QListWidget()
        self._list.setMaximumHeight(170)
        self._list.currentItemChanged.connect(self._on_item)
        self._layout.addWidget(self._list)
        self._hint = label(
            "Выделите прямоугольник мышью на кадре, чтобы добавить зону", "hint", wrap=True
        )
        self._layout.addWidget(self._hint)
        self._props = QVBoxLayout()
        self._props.setSpacing(10)
        self._layout.addLayout(self._props)
        self._layout.addStretch(1)
        self.setWidget(inner)
        self._zones: list[dict[str, Any]] = []
        self._zone: dict[str, Any] | None = None
        self._preview_box: QVBoxLayout | None = None

    # ------------------------------------------------------------------ list

    def set_zones(self, aspect: str, zones: list[dict[str, Any]], selected: str | None) -> None:
        self._title.setText(f"ЗОНЫ · {aspect}")
        self._zones = zones
        self._list.blockSignals(True)
        self._list.clear()
        current = None
        for index, zone in enumerate(zones):
            item = QListWidgetItem(caption(zone))
            item.setData(Qt.ItemDataRole.UserRole, zone["id"])
            item.setIcon(theme.icon("square-dashed", zone_color(index), 18))
            self._list.addItem(item)
            if zone["id"] == selected:
                current = item
        if current is not None:
            self._list.setCurrentItem(current)
        self._list.blockSignals(False)
        zone = next((z for z in zones if z["id"] == selected), None)
        self._show(zone)

    def refresh_list(self, zones: list[dict[str, Any]]) -> None:
        """New captions after an edit, without rebuilding the properties being edited."""
        self._zones = zones
        for index, zone in enumerate(zones):
            item = self._list.item(index)
            if item is not None:
                item.setText(caption(zone))

    def _on_item(self, item: QListWidgetItem | None, _previous: object) -> None:
        if item is not None:
            self.zone_selected.emit(item.data(Qt.ItemDataRole.UserRole))

    # ------------------------------------------------------------------ properties

    def _show(self, zone: dict[str, Any] | None) -> None:
        clear_layout(self._props)
        self._preview_box = None
        self._zone = zone
        if zone is None:
            return
        name = QLineEdit(zone.get("name", ""))
        name.setPlaceholderText(zone["id"])
        name.editingFinished.connect(lambda: self._update(name=name.text().strip()))
        top = QHBoxLayout()
        top.addWidget(name, 1)
        top.addWidget(
            icon_button("trash-2", "Удалить зону", lambda: self.zone_deleted.emit(zone["id"]))
        )
        self._props.addWidget(section("Название"))
        self._props.addLayout(top)
        if zone.get("kind") == "speaker":
            self._props.addWidget(
                label(
                    "Зона имени: читается вместе с текстовой зоной, у которой "
                    "«Кто говорит — Отдельная зона».",
                    "hint",
                    wrap=True,
                )
            )
            self._mask_controls(zone)
            self._preview_section()
            return
        policy = zone["policy"]
        trigger = zone.get("trigger", "auto")
        self._props.addWidget(section("Когда читать"))
        when = Segmented(TRIGGER, trigger)
        when.changed.connect(self._set_trigger)
        self._props.addWidget(when)
        if trigger == "hotkey":
            hotkey_row = QHBoxLayout()
            hotkey_row.addWidget(
                label(f"Клавиша: {pretty(zone['hotkey']) if zone.get('hotkey') else 'не задана'}"),
                1,
            )
            change = QPushButton("Изменить")
            change.clicked.connect(self._change_hotkey)
            hotkey_row.addWidget(change)
            self._props.addLayout(hotkey_row)
            self._hotkey_error = label("", "hint", wrap=True)
            self._props.addWidget(self._hotkey_error)
        else:
            self._props.addWidget(section("Приоритет"))
            priority = Segmented(PRIORITY, policy.get("priority", "normal"))
            priority.changed.connect(lambda v: self._update_policy(priority=v))
            self._props.addWidget(priority)
        self._props.addWidget(section("Голос"))
        voice = policy.get("voice", {"mode": "speaker"})
        voice_mode = Segmented(VOICE, voice.get("mode", "speaker"))
        voice_mode.changed.connect(self._set_voice_mode)
        self._props.addWidget(voice_mode)
        if voice.get("mode") == "fixed":
            choose = combo(voice_items(self._voices), voice.get("id") or "", "Голос зоны")
            choose.currentIndexChanged.connect(
                lambda _i: self._update_policy(voice={"mode": "fixed", "id": choose.currentData()})
            )
            self._props.addWidget(choose)
        self._props.addWidget(section("Кто говорит"))
        speaker = policy.get("speaker", {"mode": "none"})
        who = Segmented(SPEAKER, speaker.get("mode", "none"))
        who.changed.connect(self._set_speaker_mode)
        self._props.addWidget(who)
        if speaker.get("mode") == "zone":
            names = [z for z in self._zones if z.get("kind") == "speaker"]
            if names:
                pick = combo(
                    [(z.get("name") or z["id"], z["id"]) for z in names],
                    speaker.get("zone_id") or "",
                    "Зона имени",
                )
                pick.currentIndexChanged.connect(
                    lambda _i: self._update_policy(
                        speaker={"mode": "zone", "zone_id": pick.currentData()}
                    )
                )
                self._props.addWidget(pick)
            else:
                self._props.addWidget(
                    label("Добавьте зону «Имя говорящего» из шаблона.", "hint", wrap=True)
                )
        if speaker.get("mode") == "color":
            line = QHBoxLayout()
            line.addWidget(label("Цвет имени"), 1)
            line.addWidget(swatch(speaker.get("colors_hsv", [])))
            pick_name = QPushButton("  Пипетка")
            pick_name.setIcon(theme.icon("pipette"))
            pick_name.clicked.connect(lambda: self.pipette.emit("name"))
            line.addWidget(pick_name)
            self._props.addLayout(line)
        if trigger != "hotkey":
            self._props.addWidget(section("Не повторять"))
            cooldown = Segmented(
                [(str(v), t) for v, t in COOLDOWN], str(float(policy.get("repeat_cooldown_min", 0)))
            )
            cooldown.changed.connect(lambda v: self._update_policy(repeat_cooldown_min=float(v)))
            self._props.addWidget(cooldown)
        box, duck = toggle_row("Приглушать игру", "", bool(policy.get("duck", True)))
        duck.toggled.connect(lambda on: self._update_policy(duck=on))
        self._props.addWidget(box)
        self._mask_controls(zone)
        self._preview_section()

    def _mask_controls(self, zone: dict[str, Any]) -> None:
        mask = zone["mask"]
        self._props.addWidget(section("Выделение текста"))
        first = Segmented(PRESET[:2], mask["preset"])
        second = Segmented(PRESET[2:], mask["preset"])
        first.changed.connect(self._set_preset)
        second.changed.connect(self._set_preset)
        self._props.addWidget(first)
        self._props.addWidget(second)
        if mask["preset"] == "color":
            line = QHBoxLayout()
            line.addWidget(label("Цвет текста"), 1)
            line.addWidget(swatch(mask.get("colors_hsv", [])))
            pick = QPushButton("  Пипетка")
            pick.setIcon(theme.icon("pipette"))
            pick.clicked.connect(lambda: self.pipette.emit("text"))
            line.addWidget(pick)
            self._props.addLayout(line)
        elif mask["preset"] in DEFAULT_THRESHOLDS:
            value = mask.get("threshold") or DEFAULT_THRESHOLDS[mask["preset"]]
            slider = SliderRow("Порог яркости", 0, 255, 1, value, lambda v: f"{v:.0f}")
            slider.changed.connect(lambda v: self._update(mask={**mask, "threshold": round(v)}))
            self._props.addWidget(slider)

    def _preview_section(self) -> None:
        self._props.addWidget(section("Маска (что видит OCR)"))
        box = QVBoxLayout()
        box.addWidget(label("Обновляется после каждого изменения…", "hint"))
        self._props.addLayout(box)
        self._preview_box = box

    def show_preview(self, preview: ZonePreview) -> None:
        box = self._preview_box
        if box is None:
            return
        clear_layout(box)
        if preview.error:
            box.addWidget(label(preview.error, "hint", wrap=True))
            return
        if preview.mask is not None:
            data, width, height, stride = as_rgb_bytes(preview.mask)
            image = QImage(data, width, height, stride, QImage.Format.Format_RGB888).copy()
            picture = QLabel()
            pixmap = QPixmap.fromImage(image).scaledToWidth(
                PANEL_WIDTH - 40, Qt.TransformationMode.SmoothTransformation
            )
            picture.setPixmap(pixmap)
            picture.setStyleSheet(f"border: 1px solid {theme.BORDER_CONTROL}; border-radius: 6px;")
            box.addWidget(picture)
        text = preview.text or "текст не распознан"
        box.addWidget(label(text, "", wrap=True))
        good = preview.confidence >= 0.8
        color = theme.SUCCESS if good else theme.CHIPS["warning"].text
        stats = label(
            f"распознано · {preview.confidence:.2f} · {preview.elapsed_ms:.0f} мс", "hint"
        )
        stats.setStyleSheet(f"color: {color};")
        box.addWidget(stats)
        if preview.speaker or preview.voice:
            who = preview.speaker or "без имени"
            voice = voice_text(preview.voice, self._voices) if preview.voice else "—"
            box.addWidget(label(f"Говорящий: {who} → {voice}", "caption", wrap=True))
            box.addWidget(label(preview.voice_reason, "hint", wrap=True))
        listen = QPushButton("  Прослушать")
        listen.setIcon(theme.icon("play", theme.ACCENT))
        listen.clicked.connect(self.listen.emit)
        listen.setEnabled(bool(preview.text))
        box.addWidget(listen)

    # ------------------------------------------------------------------ edits

    def _emit(self, zone: dict[str, Any]) -> None:
        self._zone = zone
        self.zone_changed.emit(zone)

    def _update(self, **fields: Any) -> None:
        if self._zone is not None:
            self._emit({**self._zone, **fields})

    def _update_policy(self, **fields: Any) -> None:
        if self._zone is not None and self._zone.get("policy") is not None:
            self._emit({**self._zone, "policy": {**self._zone["policy"], **fields}})

    def _set_trigger(self, trigger: str) -> None:
        if self._zone is None:
            return
        zone = {**self._zone, "trigger": trigger}
        if trigger != "hotkey":
            zone["hotkey"] = None
        self._emit(zone)
        self._show(zone)

    def _set_voice_mode(self, mode: str) -> None:
        voice: dict[str, Any] = {"mode": mode}
        if mode == "fixed":
            voice["id"] = self._voices[0].id if self._voices else None
        self._update_policy(voice=voice)
        self._show(self._zone)

    def _set_speaker_mode(self, mode: str) -> None:
        speaker: dict[str, Any] = {"mode": mode}
        if mode == "zone":
            names = [z["id"] for z in self._zones if z.get("kind") == "speaker"]
            speaker["zone_id"] = names[0] if names else None
        if mode == "color":
            current = (self._zone or {}).get("policy", {}).get("speaker", {})
            speaker["colors_hsv"] = current.get("colors_hsv", [])
        self._update_policy(speaker=speaker)
        self._show(self._zone)

    def _set_preset(self, preset: str) -> None:
        if self._zone is None:
            return
        mask: dict[str, Any] = {"preset": preset}
        if preset == "color":
            mask["colors_hsv"] = self._zone["mask"].get("colors_hsv", [])
        self._update(mask=mask)
        self._show(self._zone)

    def _change_hotkey(self) -> None:
        if self._zone is None:
            return
        dialog = CaptureDialog(self._zone.get("name") or self._zone["id"], self)
        if not dialog.exec() or dialog.result_hotkey is None:
            return
        hotkey = dialog.result_hotkey
        conflict = self._taken(self._zone["id"]).get(hotkey)
        if conflict:
            self._hotkey_error.setText(f"{pretty(hotkey)} уже занято: {conflict}. Не сохранено.")
            self._hotkey_error.setStyleSheet(f"color: {theme.CHIPS['error'].text};")
            return
        zone = {**self._zone, "hotkey": hotkey}
        self._emit(zone)
        self._show(zone)

    def refresh_current(self) -> None:
        self._show(self._zone)


def caption(zone: dict[str, Any]) -> str:
    return f"{zone.get('name') or zone['id']}\n{describe(zone)}"


def describe(zone: dict[str, Any]) -> str:
    if zone.get("kind") == "speaker":
        return "имя говорящего"
    trigger = zone.get("trigger")
    policy = zone.get("policy") or {}
    if trigger == "hotkey":
        return pretty(zone["hotkey"]) if zone.get("hotkey") else "по клавише"
    if trigger == "off":
        return "выключена"
    priority = dict(PRIORITY).get(policy.get("priority", "normal"), "").lower().rstrip(".")
    return f"авто · {priority}"
