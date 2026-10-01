"""The frame with zone rectangles (docs/design/ZoneEditor.html): select, move, resize by the
corners, draw a new zone, pick a colour. Rectangles are fractions of the frame (TZ 4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from subvoicer.app.zone_tools import Image, as_rgb_bytes
from subvoicer.ui import theme

Fractions = tuple[float, float, float, float]
DIM = 0.45  # darkening of the frame outside zones (UI.md)
HANDLE = 8
MIN_SIZE = 0.01  # a new zone must be at least 1% of the frame each way


@dataclass(frozen=True)
class ZoneBox:
    id: str
    rect: Fractions
    color: str
    caption: str


class Drag(Enum):
    NONE = 0
    NEW = 1
    MOVE = 2
    CORNER = 3


class ZoneCanvas(QWidget):
    selected = Signal(str)
    moved = Signal(str, tuple)  # zone id, new fractions
    created = Signal(tuple)  # fractions of a new rectangle
    picked = Signal(int, int)  # eyedropper: pixel of the frame

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(480, 270)
        self._pixmap: QPixmap | None = None
        self._size = (1, 1)
        self._boxes: list[ZoneBox] = []
        self._selected: str | None = None
        self._drag = Drag.NONE
        self._start = QPointF()
        self._origin: Fractions | None = None
        self._corner = 0
        self._new: Fractions | None = None
        self.pipette = False

    # ------------------------------------------------------------------ content

    def set_image(self, image: Image) -> None:
        data, width, height, stride = as_rgb_bytes(image)
        self._image_bytes = data  # QImage does not copy
        qimage = QImage(data, width, height, stride, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimage)
        self._size = (width, height)
        self.update()

    def set_boxes(self, boxes: list[ZoneBox], selected: str | None) -> None:
        self._boxes = boxes
        self._selected = selected
        self.update()

    def set_pipette(self, on: bool) -> None:
        self.pipette = on
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------ geometry

    def _target(self) -> QRectF:
        width, height = self._size
        area = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        scale = min(area.width() / width, area.height() / height)
        w, h = width * scale, height * scale
        return QRectF(area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h)

    def _to_screen(self, rect: Fractions) -> QRectF:
        t = self._target()
        x1, y1, x2, y2 = rect
        return QRectF(
            t.x() + x1 * t.width(),
            t.y() + y1 * t.height(),
            (x2 - x1) * t.width(),
            (y2 - y1) * t.height(),
        )

    def _to_fraction(self, point: QPointF) -> tuple[float, float]:
        t = self._target()
        fx = (point.x() - t.x()) / t.width()
        fy = (point.y() - t.y()) / t.height()
        return min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0)

    def _corners(self, rect: QRectF) -> list[QPointF]:
        return [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]

    # ------------------------------------------------------------------ painting

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.BG_DEEP))
        if self._pixmap is None:
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Нет кадра")
            return
        target = self._target()
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        dim = QColor(0, 0, 0)
        dim.setAlphaF(DIM)
        painter.fillRect(target, dim)
        for box in self._boxes:
            area = self._to_screen(box.rect)
            painter.drawPixmap(area, self._pixmap, self._source_rect(box.rect))  # undimmed inside
            selected = box.id == self._selected
            pen = QPen(QColor(box.color), 2)
            pen.setStyle(Qt.PenStyle.SolidLine if selected else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(area)
            self._caption(painter, area, box, selected)
            if selected:
                painter.setPen(QPen(QColor(box.color), 1))
                painter.setBrush(QColor(theme.TEXT))
                for corner in self._corners(area):
                    painter.drawRect(
                        QRectF(corner.x() - HANDLE / 2, corner.y() - HANDLE / 2, HANDLE, HANDLE)
                    )
        if self._new is not None:
            pen = QPen(QColor(theme.ACCENT), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._to_screen(self._new))
        painter.end()

    def _source_rect(self, rect: Fractions) -> QRectF:
        width, height = self._size
        x1, y1, x2, y2 = rect
        return QRectF(x1 * width, y1 * height, (x2 - x1) * width, (y2 - y1) * height)

    def _caption(self, painter: QPainter, area: QRectF, box: ZoneBox, selected: bool) -> None:
        text = box.caption
        if selected:
            x1, y1, x2, y2 = box.rect
            text = f"{box.caption}   {x1:.2f}, {y1:.2f} → {x2:.2f}, {y2:.2f}"
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 16
        tag = QRectF(area.x(), area.y() - 24, width, 20)
        if tag.y() < 0:
            tag.moveTop(area.bottom() + 4)
        color = QColor(box.color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color if selected else QColor(theme.BG_WINDOW))
        painter.drawRoundedRect(tag, 5, 5)
        painter.setPen(QColor(theme.ON_ACCENT) if selected else color)
        painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)

    # ------------------------------------------------------------------ mouse

    def _hit(self, point: QPointF) -> tuple[str | None, int | None]:
        """(zone id, corner index) under the point; the selected zone wins."""
        ordered = sorted(self._boxes, key=lambda b: b.id != self._selected)
        for box in ordered:
            area = self._to_screen(box.rect)
            if box.id == self._selected:
                for index, corner in enumerate(self._corners(area)):
                    if (
                        abs(corner.x() - point.x()) <= HANDLE
                        and abs(corner.y() - point.y()) <= HANDLE
                    ):
                        return box.id, index
            if area.contains(point):
                return box.id, None
        return None, None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._pixmap is None or event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        if self.pipette:
            fx, fy = self._to_fraction(point)
            width, height = self._size
            self.picked.emit(round(fx * (width - 1)), round(fy * (height - 1)))
            return
        zone_id, corner = self._hit(point)
        self._start = point
        if zone_id is None:
            self._drag = Drag.NEW
            fx, fy = self._to_fraction(point)
            self._new = (fx, fy, fx, fy)
            return
        self._selected = zone_id
        self.selected.emit(zone_id)
        box = next(b for b in self._boxes if b.id == zone_id)
        self._origin = box.rect
        if corner is not None:
            self._drag, self._corner = Drag.CORNER, corner
        else:
            self._drag = Drag.MOVE
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position()
        if self._drag is Drag.NONE:
            if not self.pipette:
                zone_id, corner = self._hit(point)
                shape = Qt.CursorShape.ArrowCursor
                if corner is not None:
                    shape = (
                        Qt.CursorShape.SizeFDiagCursor
                        if corner in (0, 2)
                        else Qt.CursorShape.SizeBDiagCursor
                    )
                elif zone_id is not None:
                    shape = Qt.CursorShape.SizeAllCursor
                self.setCursor(shape)
            return
        fx, fy = self._to_fraction(point)
        if self._drag is Drag.NEW and self._new is not None:
            sx, sy = self._new[0], self._new[1]
            self._new = (sx, sy, fx, fy)
            self.update()
            return
        if self._origin is None or self._selected is None:
            return
        x1, y1, x2, y2 = self._origin
        if self._drag is Drag.MOVE:
            sx, sy = self._to_fraction(self._start)
            dx = min(max(fx - sx, -x1), 1 - x2)
            dy = min(max(fy - sy, -y1), 1 - y2)
            rect = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
        else:
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            opposite = corners[(self._corner + 2) % 4]
            rect = (
                min(opposite[0], fx),
                min(opposite[1], fy),
                max(opposite[0], fx),
                max(opposite[1], fy),
            )
        self._boxes = [
            ZoneBox(b.id, rect, b.color, b.caption) if b.id == self._selected else b
            for b in self._boxes
        ]
        self.update()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        drag, self._drag = self._drag, Drag.NONE
        if drag is Drag.NEW and self._new is not None:
            x1, y1, x2, y2 = self._new
            self._new = None
            rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            if rect[2] - rect[0] >= MIN_SIZE and rect[3] - rect[1] >= MIN_SIZE:
                self.created.emit(tuple(round(v, 4) for v in rect))
            self.update()
            return
        if drag in (Drag.MOVE, Drag.CORNER) and self._selected is not None:
            box = next((b for b in self._boxes if b.id == self._selected), None)
            if box is not None:
                x1, y1, x2, y2 = box.rect
                if x2 - x1 >= MIN_SIZE and y2 - y1 >= MIN_SIZE:
                    self.moved.emit(self._selected, tuple(round(v, 4) for v in box.rect))
