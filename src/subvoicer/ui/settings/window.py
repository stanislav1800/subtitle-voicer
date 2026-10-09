"""Settings window: sidebar navigation + pages (TZ 4.13, docs/design/Main.html)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app import runtime
from subvoicer.app.facade import AppFacade
from subvoicer.ui import theme
from subvoicer.ui.widgets.common import label

WINDOW_SIZE = (1100, 720)
SIDEBAR_WIDTH = 240


class Page(QScrollArea):
    """A tab: big title, a muted subtitle, then content; scrolls when taller than the window."""

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.content = QVBoxLayout(inner)
        self.content.setContentsMargins(32, 28, 32, 28)
        self.content.setSpacing(16)
        self.content.addWidget(label(title, "pageTitle"))
        if subtitle:
            self.content.addWidget(label(subtitle, "caption"))
        self.setWidget(inner)

    def finish(self) -> None:
        self.content.addStretch(1)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.fit_height()

    def fit_height(self) -> None:
        """Wrapped labels need their height for the actual width, or the cards get squeezed
        instead of the page scrolling."""
        inner = self.widget()
        layout = inner.layout()
        width = self.viewport().width()
        if layout is not None and layout.hasHeightForWidth():
            inner.setMinimumHeight(layout.totalHeightForWidth(width))
        else:
            inner.setMinimumHeight(inner.sizeHint().height())

    def refresh(self) -> None:
        """Called when the page is shown: re-read what may have changed."""


class SettingsWindow(QWidget):
    def __init__(self, facade: AppFacade) -> None:
        super().__init__()
        self._facade = facade
        self.setObjectName("window")
        self.setWindowTitle("Subtitle Voicer")
        self.setWindowIcon(theme.app_icon())
        self.resize(*WINDOW_SIZE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 16)
        side.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(24, 24))
        brand.addWidget(logo)
        brand.addWidget(label("SUBTITLE VOICER", "sectionTitle"), 1)
        side.addLayout(brand)
        side.addSpacing(16)
        self._nav = QButtonGroup(self)
        self._nav_layout = side
        self._stack = QStackedWidget()
        self._pages: dict[str, tuple[QPushButton, Page]] = {}
        side.addStretch(1)
        self._status_card = QFrame()
        self._status_card.setObjectName("statusCard")
        card = QVBoxLayout(self._status_card)
        card.setContentsMargins(12, 10, 12, 10)
        card.setSpacing(4)
        self._status_title = label("", "")
        self._status_lines = label("", "hint", wrap=True)
        card.addWidget(self._status_title)
        card.addWidget(self._status_lines)
        side.addWidget(self._status_card)

        layout.addWidget(sidebar)
        layout.addWidget(self._stack, 1)
        self._stack.currentChanged.connect(self._on_page)

    def add_page(self, key: str, title: str, icon_name: str, page: Page) -> None:
        button = QPushButton(f"  {title}")
        button.setObjectName("nav")
        button.setCheckable(True)
        button.setIcon(theme.icon(icon_name))
        button.clicked.connect(lambda _=False, k=key: self.show_page(k))
        self._nav.addButton(button)
        self._nav_layout.insertWidget(self._nav_layout.count() - 2, button)
        self._stack.addWidget(page)
        self._pages[key] = (button, page)
        if len(self._pages) == 1:
            button.setChecked(True)

    def set_page_visible(self, key: str, visible: bool) -> None:
        button, page = self._pages[key]
        button.setVisible(visible)
        if not visible and self._stack.currentWidget() is page:
            self.show_page(next(iter(self._pages)))

    def page_keys(self) -> list[str]:
        return [key for key, (button, _) in self._pages.items() if not button.isHidden()]

    def current_page(self) -> Page | None:
        page = self._stack.currentWidget()
        return page if isinstance(page, Page) else None

    def show_page(self, key: str) -> None:
        button, page = self._pages[key]
        button.setChecked(True)
        self._stack.setCurrentWidget(page)

    def bring_up(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def refresh_status(self) -> None:
        status = self._facade.status()
        dot = theme.SUCCESS if status.enabled and status.has_zones else theme.TEXT_MUTED
        if not self._facade.ready:
            title, lines = "Загрузка…", "OCR и голоса загружаются"
        elif status.title is None:
            title, lines = "Ждёт игру", "Игра с профилем появится, когда окажется в фокусе"
        elif not status.enabled:
            title, lines = "Автоозвучка выключена", f"Профиль: {status.title}"
        elif not status.has_zones:
            title, lines = "Нет зон", f"Профиль: {status.title}\nЗоны ещё не заданы"
        else:
            window = status.window
            size = f" · {window.width}×{window.height}" if window else ""
            title = "Озвучка работает"
            lines = f"Профиль: {status.title}\nОкно игры{size}"
        self._status_title.setText(f"<span style='color:{dot}'>●</span>&nbsp; <b>{title}</b>")
        self._status_lines.setText(lines)

    def _on_page(self, _index: int) -> None:
        page = self._stack.currentWidget()
        if isinstance(page, Page):
            page.refresh()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        runtime.dark_title_bar(int(self.winId()))
        self.refresh_status()
        self._on_page(self._stack.currentIndex())

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing the window hides it to the tray; «Выход» is in the tray menu."""
        event.ignore()
        self.hide()
