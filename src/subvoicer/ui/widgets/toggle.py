"""ToggleSwitch: the one custom-painted control allowed by docs/UI.md (44x24)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget

from subvoicer.ui import theme


class ToggleSwitch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None, *, checked: bool = False) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 1, 42, 22)
        on = self.isChecked()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT if on else theme.BORDER_CONTROL))
        if not self.isEnabled():
            painter.setOpacity(0.5)
        painter.drawRoundedRect(track, 11, 11)
        knob = QRectF(23 if on else 4, 4, 16, 16)
        painter.setBrush(QColor(theme.ON_ACCENT if on else theme.TEXT_SECONDARY))
        painter.drawEllipse(knob)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.ACCENT_HOVER), 2))
            painter.drawRoundedRect(QRectF(1, 1, 42, 22), 11, 11)
        painter.end()
