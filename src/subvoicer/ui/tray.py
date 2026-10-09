"""Tray icon and its menu (TZ 4.13, docs/design/Tray.html)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from subvoicer.app.facade import AppFacade, GameStatus
from subvoicer.core.models import HistoryEntry, WindowRef
from subvoicer.ui import theme
from subvoicer.ui.widgets.common import label
from subvoicer.ui.widgets.toggle import ToggleSwitch

RECENT_COUNT = 5
PREVIEW_CHARS = 48


def status_text(status: GameStatus, ready: bool) -> str:
    if not ready:
        return "Загрузка OCR и голосов…"
    if status.title is None:
        return "Нет активной игры"
    if not status.has_zones:
        return f"{status.title} · нет зон"
    return status.title


def entry_caption(entry: HistoryEntry) -> str:
    text = entry.text if len(entry.text) <= PREVIEW_CHARS else entry.text[:PREVIEW_CHARS] + "…"
    return f"{entry.speaker or 'Рассказчик'}  {text}"


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        facade: AppFacade,
        open_settings: Callable[[], None],
        open_line: Callable[[HistoryEntry], None],
        open_zone_editor: Callable[[], None],
        quit_app: Callable[[], None],
    ) -> None:
        super().__init__(theme.app_icon())
        self._facade = facade
        self._open_line = open_line
        self.setToolTip("Subtitle Voicer")
        menu = QMenu()
        self._menu = menu

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(2)
        header_layout.addWidget(label("SUBTITLE VOICER", "sectionTitle"))
        self._status = label("", "caption")
        header_layout.addWidget(self._status)
        header_action = QWidgetAction(menu)
        header_action.setDefaultWidget(header)
        menu.addAction(header_action)
        menu.addSeparator()

        toggle_box = QWidget()
        toggle_layout = QHBoxLayout(toggle_box)
        toggle_layout.setContentsMargins(12, 6, 12, 6)
        toggle_layout.addWidget(QLabel("Автоозвучка"), 1)
        self._toggle = ToggleSwitch(checked=facade.settings.enabled)
        self._toggle.setAccessibleName("Автоозвучка")
        self._toggle.toggled.connect(facade.set_enabled)
        toggle_layout.addWidget(self._toggle)
        toggle_action = QWidgetAction(menu)
        toggle_action.setDefaultWidget(toggle_box)
        menu.addAction(toggle_action)

        hotkeys = facade.settings.hotkeys
        self._repeat = menu.addAction(f"Повторить последнюю\t{pretty(hotkeys['repeat'])}")
        self._repeat.triggered.connect(facade.repeat)
        self._recent = menu.addMenu("Последние реплики")
        self._recent.aboutToShow.connect(self._fill_recent)
        self._zones = menu.addAction(f"Настроить зоны\t{pretty(hotkeys['zone_editor'])}")
        self._zones.triggered.connect(open_zone_editor)
        self._offer = menu.addAction("Создать профиль")
        self._offer.setVisible(False)
        self._offered: WindowRef | None = None
        self._create: Callable[[WindowRef], None] | None = None
        self._offer.triggered.connect(self._create_offered)
        menu.addSeparator()
        menu.addAction("Настройки…").triggered.connect(open_settings)
        menu.addSeparator()
        menu.addAction("Выход").triggered.connect(quit_app)
        menu.aboutToShow.connect(self.refresh)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self._open_settings = open_settings
        self.refresh()

    def refresh(self) -> None:
        status = self._facade.status()
        ready = self._facade.ready
        self._status.setText(status_text(status, ready))
        self._toggle.blockSignals(True)
        self._toggle.setChecked(self._facade.settings.enabled)
        self._toggle.blockSignals(False)
        self._zones.setEnabled(status.window is not None)
        self.setToolTip(f"Subtitle Voicer — {status_text(status, ready)}")

    def offer(self, window: WindowRef | None, create: Callable[[WindowRef], None]) -> None:
        """«Создать профиль: game.exe» for a game offered by a notification (None hides it)."""
        self._offered, self._create = window, create
        self._offer.setVisible(window is not None)
        if window is not None:
            self._offer.setText(f"Создать профиль: {window.exe}")

    def _create_offered(self) -> None:
        if self._offered is not None and self._create is not None:
            self._create(self._offered)

    def _fill_recent(self) -> None:
        self._recent.clear()
        entries = self._facade.recent(RECENT_COUNT)
        if not entries:
            empty = self._recent.addAction("Пока ничего не озвучено")
            empty.setEnabled(False)
            return
        for entry in entries:
            action = QAction(entry_caption(entry), self._recent)
            action.triggered.connect(lambda _=False, e=entry: self._open_line(e))
            self._recent.addAction(action)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._open_settings()


def pretty(hotkey: str) -> str:
    """``<ctrl>+<alt>+r`` -> ``Ctrl+Alt+R``."""
    parts = []
    for part in hotkey.split("+"):
        name = part.strip("<>")
        parts.append(name.capitalize() if len(name) > 1 else name.upper())
    return "+".join(parts)
