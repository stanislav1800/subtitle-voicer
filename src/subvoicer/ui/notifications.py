"""Cards near the tray with 1-2 buttons (TZ 4.13, docs/design/Tray.html): Windows toasts do
not support buttons from Qt, so this is a frameless tool window (UI.md)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from subvoicer.app import runtime
from subvoicer.ui import theme
from subvoicer.ui.widgets.common import icon_button, label, primary

WIDTH = 380
MARGIN = 16
INFO_TIMEOUT_MS = 12_000

Action = tuple[str, Callable[[], None]]


class Notification(QWidget):
    def __init__(
        self,
        heading: str,
        body: str,
        primary_action: Action | None = None,
        secondary_action: Action | None = None,
        timeout_ms: int | None = INFO_TIMEOUT_MS,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # the game keeps focus
        self.setFixedWidth(WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(
            f"QFrame#card {{ background: {theme.BG_WINDOW};"
            f" border: 1px solid {theme.BORDER_CONTROL}; border-radius: 12px; }}"
        )
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(18, 18))
        head.addWidget(logo)
        head.addWidget(label("Subtitle Voicer", "caption"), 1)
        head.addWidget(icon_button("x", "Закрыть", self.close))
        layout.addLayout(head)
        title = label(heading, "")
        title.setStyleSheet("font-weight: 700; font-size: 15px;")
        layout.addWidget(title)
        text = label(body, "caption", wrap=True)
        text.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(text)
        if primary_action or secondary_action:
            buttons = QHBoxLayout()
            if primary_action is not None:
                buttons.addWidget(primary(primary_action[0], self._run(primary_action[1])), 1)
            if secondary_action is not None:
                second = QPushButton(secondary_action[0])
                second.clicked.connect(self._run(secondary_action[1]))
                buttons.addWidget(second, 1)
            layout.addLayout(buttons)
        if timeout_ms:
            QTimer.singleShot(timeout_ms, self.close)

    def _run(self, action: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            self.close()
            action()

        return run

    def popup(self, stack_index: int = 0) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        if area is not None:
            x = area.right() - self.width() - MARGIN
            y = area.bottom() - (self.height() + MARGIN) * (stack_index + 1)
            self.move(x, y)
        self.show()
        runtime.keep_topmost(int(self.winId()))
        runtime.exclude_from_capture(int(self.winId()))  # not read by the screen capture


class Notifier:
    """Keeps the visible cards stacked above each other."""

    def __init__(self) -> None:
        self._shown: list[Notification] = []

    def show(self, notification: Notification) -> None:
        self._shown = [n for n in self._shown if n.isVisible()]
        notification.destroyed.connect(lambda: self._forget(notification))
        notification.popup(len(self._shown))
        self._shown.append(notification)

    def _forget(self, notification: Notification) -> None:
        self._shown = [n for n in self._shown if n is not notification]


def mono(text: str) -> str:
    style = f"font-family: '{theme.family(theme.MONO_FAMILY)}'; color: {theme.ACCENT_HOVER};"
    return f'<span style="{style}">{text}</span>'
