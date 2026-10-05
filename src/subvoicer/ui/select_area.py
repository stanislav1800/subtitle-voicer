"""«Выделить и прочитать» (Ctrl+Alt+A, TZ 4.12): the frozen game frame over the game, drag a
rectangle — it is read once. Esc or a right click cancels."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from subvoicer.app import runtime
from subvoicer.app.zone_tools import Image, as_rgb_bytes
from subvoicer.ui import theme
from subvoicer.ui.widgets.screens import PhysicalRect, screen_for

DIM = 0.45
MIN_SIDE_PX = 8  # a click, not a selection
HINT = "Выделите текст мышью — он прочитается один раз  ·  Esc — отмена"

Fractions = tuple[float, float, float, float]


def to_fractions(selection: QRectF, target: QRectF) -> Fractions:
    """A selection in widget pixels -> fractions of the frame drawn at ``target``."""
    area = selection.normalized().intersected(target)

    def fx(x: float) -> float:
        return min(1.0, max(0.0, (x - target.left()) / target.width()))

    def fy(y: float) -> float:
        return min(1.0, max(0.0, (y - target.top()) / target.height()))

    return fx(area.left()), fy(area.top()), fx(area.right()), fy(area.bottom())


def frame_target(
    widget: QRectF,
    size: tuple[int, int],
    client: PhysicalRect | None,
    monitor: PhysicalRect | None,
    ratio: float,
) -> QRectF:
    """Where the frame goes: exactly over the game image, or fitted when that is unknown."""
    if client is not None and monitor is not None:
        left, top, right, bottom = client
        return QRectF(
            (left - monitor[0]) / ratio,
            (top - monitor[1]) / ratio,
            (right - left) / ratio,
            (bottom - top) / ratio,
        )
    width, height = size
    scale = min(widget.width() / width, widget.height() / height)
    w, h = width * scale, height * scale
    return QRectF((widget.width() - w) / 2, (widget.height() - h) / 2, w, h)


class AreaSelector(QWidget):
    def __init__(
        self,
        frame: Image,
        client: PhysicalRect | None,
        monitor: PhysicalRect | None,
        on_selected: Callable[[Fractions], None],
    ) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Выделить и прочитать")
        self.setCursor(Qt.CursorShape.CrossCursor)
        data, width, height, stride = as_rgb_bytes(frame)
        self._image = QImage(data, width, height, stride, QImage.Format.Format_RGB888).copy()
        self._size = (width, height)
        self._client = client
        self._monitor = monitor
        self._on_selected = on_selected
        self._target = QRectF()
        self._start: QPointF | None = None
        self._end: QPointF | None = None

    def open(self) -> None:
        screen = screen_for(self._monitor)
        ratio = 1.0
        if screen is not None:
            self.setScreen(screen)
            self.setGeometry(screen.geometry())
            ratio = screen.devicePixelRatio()
        self.showFullScreen()
        self._target = frame_target(
            QRectF(self.rect()), self._size, self._client, self._monitor, ratio
        )
        self.raise_()
        self.activateWindow()
        runtime.force_foreground(int(self.winId()))  # over the game that has the focus

    def _selection(self) -> QRectF | None:
        if self._start is None or self._end is None:
            return None
        return QRectF(self._start, self._end).normalized().intersected(self._target)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.BG_DEEP))
        painter.drawImage(self._target, self._image)
        dim = QColor(0, 0, 0)
        dim.setAlphaF(DIM)
        painter.fillRect(self.rect(), dim)
        selection = self._selection()
        if selection is not None and not selection.isEmpty():
            fractions = to_fractions(selection, self._target)
            width, height = self._size
            source = QRectF(
                fractions[0] * width,
                fractions[1] * height,
                (fractions[2] - fractions[0]) * width,
                (fractions[3] - fractions[1]) * height,
            )
            painter.drawImage(selection, self._image, source)  # undimmed inside
            painter.setPen(QPen(QColor(theme.ACCENT), 2))
            painter.drawRect(selection)
        painter.setFont(theme.font(theme.BODY_FAMILY, 15, 500))
        bar = QRectF(0, 24, self.width(), 40)
        metrics = painter.fontMetrics()
        pill = QRectF(0, 0, metrics.horizontalAdvance(HINT) + 40, 40)
        pill.moveCenter(bar.center())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BG_WINDOW))
        painter.drawRoundedRect(pill, 8, 8)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, HINT)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = self._end = event.position()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._start is not None:
            self._end = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        self._end = event.position()
        selection = self._selection()
        if selection is None or min(selection.width(), selection.height()) < MIN_SIDE_PX:
            self._start = self._end = None  # a click: select again
            self.update()
            return
        fractions = to_fractions(selection, self._target)
        self.close()
        self._on_selected(fractions)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
