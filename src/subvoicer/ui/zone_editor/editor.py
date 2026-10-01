"""Zone editor window: over the game (frozen frame on the game's monitor) or on a screenshot
(TZ 4.13, docs/design/ZoneEditor.html, Screenshot.html)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app import profile_commands as pc
from subvoicer.app import zone_tools
from subvoicer.app.facade import AppFacade
from subvoicer.config.profile import Layout, Profile, Zone
from subvoicer.config.profile_store import format_error
from subvoicer.ui import theme
from subvoicer.ui.settings.hotkeys import ACTIONS
from subvoicer.ui.widgets.common import chip, label, primary
from subvoicer.ui.widgets.screens import screen_for
from subvoicer.ui.zone_editor.canvas import Fractions, ZoneBox, ZoneCanvas
from subvoicer.ui.zone_editor.panel import TEMPLATE_TITLES, ZonePanel, describe, zone_color

log = logging.getLogger(__name__)

PREVIEW_DELAY_MS = 400
DEFAULT_RECTS: dict[str, Fractions] = {
    "dialog": (0.2, 0.82, 0.8, 0.95),
    "note": (0.3, 0.15, 0.7, 0.8),
    "notification": (0.62, 0.05, 0.98, 0.15),
    "objective": (0.75, 0.16, 0.98, 0.26),
    "speaker": (0.2, 0.77, 0.5, 0.82),
}


class ZoneEditor(QWidget):
    def __init__(
        self,
        facade: AppFacade,
        path: Path,
        frame: zone_tools.Image,
        mode: str,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._facade = facade
        self._path = path
        self._frame = frame
        self._mode = mode
        height, width = frame.shape[:2]
        self._aspect = zone_tools.aspect_of(width, height)
        self._profile: Profile = facade.profile(path)
        layout = self._profile.layouts.get(self._aspect)
        self._zones: list[dict[str, Any]] = [z.model_dump() for z in layout.zones] if layout else []
        self._selected: str | None = self._zones[0]["id"] if self._zones else None
        self._template = "dialog"
        self._pipette_target = ""
        self.setObjectName("window")
        self.setWindowTitle("Редактор зон")
        self.setWindowIcon(theme.app_icon())

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(20, 10, 20, 10)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Редактор зон", "windowTitle"))
        exe = self._profile.exe or path.stem
        titles.addWidget(label(f"{exe} · {self._aspect} · {width}×{height}", "mono"))
        bar_layout.addLayout(titles)
        bar_layout.addWidget(chip("Поверх игры" if mode == "game" else "По скриншоту", "info"))
        self._message = label(
            "Выделите прямоугольник мышью, чтобы добавить зону", "hint", wrap=True
        )
        bar_layout.addWidget(self._message, 1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.close)
        bar_layout.addWidget(cancel)
        bar_layout.addWidget(primary("Сохранить", self._save))
        root.addWidget(bar)
        for warning in warnings or []:
            note = label(warning, "", wrap=True)
            note.setStyleSheet(
                theme.chip_style("warning") + " border-radius: 0; padding: 6px 20px;"
            )
            root.addWidget(note)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._canvas = ZoneCanvas()
        self._canvas.set_image(frame)
        self._canvas.selected.connect(self._select)
        self._canvas.moved.connect(self._moved)
        self._canvas.created.connect(self._created)
        self._canvas.picked.connect(self._picked)
        body.addWidget(self._canvas, 1)
        self._panel = ZonePanel(facade.voices(), self._taken_hotkeys)
        self._panel.template_chosen.connect(self._add_template)
        self._panel.zone_selected.connect(self._select)
        self._panel.zone_changed.connect(self._changed)
        self._panel.zone_deleted.connect(self._delete)
        self._panel.pipette.connect(self._start_pipette)
        self._panel.listen.connect(self._listen)
        body.addWidget(self._panel)
        root.addLayout(body, 1)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._preview)
        self._last_preview: zone_tools.ZonePreview | None = None
        self._render(full=True)

    # ------------------------------------------------------------------ showing

    def open(self) -> None:
        if self._mode == "game":
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            screen = screen_for(self._facade.game_monitor())
            if screen is not None:
                self.setScreen(screen)
                self.setGeometry(screen.geometry())
            self.showFullScreen()
        else:
            self.resize(1400, 860)
            self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._canvas.pipette:
                self._canvas.set_pipette(False)
                self._message.setText("Пипетка отменена")
            else:
                self.close()
            return
        super().keyPressEvent(event)

    def _render(self, *, full: bool = False) -> None:
        boxes = [
            ZoneBox(
                z["id"],
                tuple(z["rect"]),
                zone_color(i),
                f"{z.get('name') or z['id']} · {describe(z)}",
            )
            for i, z in enumerate(self._zones)
        ]
        self._canvas.set_boxes(boxes, self._selected)
        if full:
            self._panel.set_zones(self._aspect, self._zones, self._selected)
        self._schedule_preview()

    def _zone(self, zone_id: str | None) -> dict[str, Any] | None:
        return next((z for z in self._zones if z["id"] == zone_id), None)

    # ------------------------------------------------------------------ edits

    def _select(self, zone_id: str) -> None:
        if zone_id != self._selected:
            self._selected = zone_id
            self._render(full=True)

    def _moved(self, zone_id: str, rect: tuple[float, ...]) -> None:
        zone = self._zone(zone_id)
        if zone is not None:
            zone["rect"] = list(rect)
            self._render(full=False)

    def _changed(self, zone: dict[str, Any]) -> None:
        self._zones = [zone if z["id"] == zone["id"] else z for z in self._zones]
        self._render(full=False)
        self._panel.refresh_list(self._zones)

    def _created(self, rect: tuple[float, ...]) -> None:
        self._add_template(self._template, rect)

    def _add_template(self, template: str, rect: tuple[float, ...] | None = None) -> None:
        self._template = template
        data = dict(pc.zone_templates()[template])
        taken = {z["id"] for z in self._zones}
        new_id, counter = template, 2
        while new_id in taken:
            new_id, counter = f"{template}{counter}", counter + 1
        data.update(id=new_id, rect=list(rect or DEFAULT_RECTS[template]))
        if data.get("trigger") == "hotkey":
            data["hotkey"] = self._free_zone_hotkey()
        try:
            zone = Zone.model_validate(data).model_dump()
        except ValueError as exc:
            self._message.setText(f"Зона не создана: {format_error(exc)}")
            return
        self._zones.append(zone)
        self._selected = new_id
        self._message.setText(f"Добавлена зона «{TEMPLATE_TITLES[template]}» — настройте её справа")
        self._render(full=True)

    def _delete(self, zone_id: str) -> None:
        self._zones = [z for z in self._zones if z["id"] != zone_id]
        self._selected = self._zones[0]["id"] if self._zones else None
        self._render(full=True)

    def _start_pipette(self, target: str) -> None:
        self._pipette_target = target
        self._canvas.set_pipette(True)
        what = "текста" if target == "text" else "имени"
        self._message.setText(f"Щёлкните по букве {what} на кадре (Esc — отмена)")

    def _picked(self, x: int, y: int) -> None:
        self._canvas.set_pipette(False)
        zone = self._zone(self._selected)
        if zone is None:
            return
        ranges = [[list(lo), list(hi)] for lo, hi in zone_tools.colors_around(self._frame, x, y)]
        if self._pipette_target == "name" and zone.get("policy"):
            speaker = {**zone["policy"]["speaker"], "mode": "color", "colors_hsv": ranges}
            zone = {**zone, "policy": {**zone["policy"], "speaker": speaker}}
        else:
            zone = {**zone, "mask": {**zone["mask"], "preset": "color", "colors_hsv": ranges}}
        self._zones = [zone if z["id"] == zone["id"] else z for z in self._zones]
        self._message.setText("Цвет выбран — проверьте маску справа")
        self._render(full=True)

    def _taken_hotkeys(self, zone_id: str) -> dict[str, str]:
        """Combos a zone hotkey must not reuse: the general ones and other zones of this layout."""
        titles = {key: title for key, title, _, _ in ACTIONS}
        taken = {
            combo: titles.get(key, key) for key, combo in self._facade.settings.hotkeys.items()
        }
        for zone in self._zones:
            if zone["id"] != zone_id and zone.get("hotkey"):
                taken[zone["hotkey"]] = f"зона «{zone.get('name') or zone['id']}»"
        return taken

    def _free_zone_hotkey(self) -> str:
        taken = self._taken_hotkeys("")
        for digit in range(1, 10):
            combo = f"<ctrl>+<alt>+{digit}"
            if combo not in taken:
                return combo
        return "<ctrl>+<alt>+0"

    # ------------------------------------------------------------------ preview, save

    def _schedule_preview(self) -> None:
        self._preview_timer.start()

    def _validated(self) -> list[Zone] | None:
        try:
            return Layout(zones=[Zone.model_validate(z) for z in self._zones]).zones
        except ValueError as exc:
            self._message.setText(format_error(exc))
            return None

    def _preview(self) -> None:
        zone_data = self._zone(self._selected)
        zones = self._validated()
        if zone_data is None or zones is None:
            return
        zone = next(z for z in zones if z.id == zone_data["id"])
        preview = self._facade.preview_zone(self._frame, zone, zones, self._profile)
        self._last_preview = preview
        self._panel.show_preview(preview)

    def _listen(self) -> None:
        preview = self._last_preview
        if preview is None or not preview.text:
            return
        voice = preview.voice or self._profile.narrator_voice
        self._facade.say(preview.text, voice, preview.speaker)

    def _save(self) -> None:
        zones = self._validated()
        if zones is None:
            return
        aspect = self._aspect

        def change(profile: Profile) -> Profile:
            layouts = {**profile.layouts, aspect: Layout(zones=zones)}
            return profile.model_copy(update={"layouts": layouts})

        try:
            self._facade.update_profile(self._path, change)
        except (OSError, ValueError) as exc:
            self._message.setText(f"Не сохранено: {format_error(exc)}")
            return
        log.info("Зоны %s сохранены: %s, %d зон", self._path.name, aspect, len(zones))
        self.close()
