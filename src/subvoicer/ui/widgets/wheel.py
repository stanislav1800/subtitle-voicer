"""The mouse wheel scrolls the page, not the field under the cursor.

A wheel turn over a drop-down, slider or number field changed its value while the user only
scrolled the settings page down. Now such a field takes the wheel only once it has the focus
(clicked or reached with Tab); otherwise the turn goes on to the page."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QWidget,
)

FIELDS = (QComboBox, QAbstractSlider, QAbstractSpinBox)


class WheelGuard(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, FIELDS):
            return False
        if event.type() == QEvent.Type.Polish:
            # the wheel must not give the focus by itself (Qt.WheelFocus of a drop-down)
            watched.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        elif event.type() == QEvent.Type.Wheel and not watched.hasFocus():
            page = _scrolling_parent(watched)
            if page is not None:
                QCoreApplication.sendEvent(page.viewport(), event)
            return True
        return False


def _scrolling_parent(widget: QWidget) -> QAbstractScrollArea | None:
    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, QAbstractScrollArea):
        parent = parent.parentWidget()
    return parent


def install_wheel_guard(app: QApplication) -> WheelGuard:
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
