"""Developer overlay over the game (TZ 4.13.2, docs/design/Overlay.html): a panel with metrics
and thin zone frames; transparent for clicks. An owned window of the game's window: right above
the game, but under any window put over it (no injection into the game, see CLAUDE.md)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QScreen
from PySide6.QtWidgets import QWidget

from subvoicer.app import runtime
from subvoicer.app.facade import AppFacade, DebugState
from subvoicer.ui import theme
from subvoicer.ui.widgets.screens import screen_for

REFRESH_MS = 500
PANEL = QRectF(16, 16, 340, 196)


def to_logical(
    screen: QScreen, physical: tuple[int, int, int, int], monitor: tuple[int, int, int, int]
) -> QRectF:
    """Physical screen pixels (DPI awareness v2) -> Qt coordinates of ``screen``."""
    ratio = screen.devicePixelRatio()
    origin = screen.geometry().topLeft()
    x1, y1, x2, y2 = physical
    mx, my = monitor[0], monitor[1]
    return QRectF(
        origin.x() + (x1 - mx) / ratio,
        origin.y() + (y1 - my) / ratio,
        (x2 - x1) / ratio,
        (y2 - y1) / ratio,
    )


class Overlay(QWidget):
    def __init__(self, facade: AppFacade) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._facade = facade
        self._state: DebugState | None = None
        self._game = QRectF()
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)

    def sync(self) -> None:
        """Shown only with developer mode, the overlay setting and an active game."""
        settings = self._facade.settings
        wanted = (
            settings.dev_mode
            and settings.debug_overlay
            and self._facade.status().window is not None
        )
        if wanted:
            self._timer.start()
            self._tick()
        else:
            self._timer.stop()
            self.hide()

    def _tick(self) -> None:
        state = self._facade.debug_state()
        self._state = state
        screen = screen_for(state.monitor_rect)
        if screen is None:
            return
        if self.geometry() != screen.geometry():
            self.setGeometry(screen.geometry())
        if state.client_rect is not None and state.monitor_rect is not None:
            game = to_logical(screen, state.client_rect, state.monitor_rect)
            self._game = game.translated(-screen.geometry().x(), -screen.geometry().y())
        else:
            self._game = QRectF(self.rect())
        if not self.isVisible():
            self.show()
        window = state.status.window
        if window is not None:  # above the game only, not above everything
            runtime.own_by(int(self.winId()), window.hwnd)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        state = self._state
        if state is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mono = theme.mono(11)
        painter.setFont(mono)
        game = self._game
        for index, zone in enumerate(state.zones):
            x1, y1, x2, y2 = zone.rect
            area = QRectF(
                game.x() + x1 * game.width(),
                game.y() + y1 * game.height(),
                (x2 - x1) * game.width(),
                (y2 - y1) * game.height(),
            )
            color = QColor(theme.ZONE_COLORS[index % len(theme.ZONE_COLORS)])
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(area)
            painter.drawText(QPointF(area.x(), area.y() - 5), f"{zone.id} · {zone.state}")
        self._panel(painter, state)
        painter.end()

    def _panel(self, painter: QPainter, state: DebugState) -> None:
        background = QColor(theme.BG_DEEP)
        background.setAlphaF(0.88)
        painter.setPen(QPen(QColor(theme.BORDER_CONTROL), 1))
        painter.setBrush(background)
        painter.drawRoundedRect(PANEL, 10, 10)
        status = state.status
        last = state.last
        working = status.enabled and status.has_zones
        lines = [
            ("профиль", f"{status.title or '—'} · {status.aspect or '—'}"),
            ("зоны", ", ".join(f"{z.id}·{z.state}" for z in state.zones) or "—"),
            ("OCR", f"{last.confidence:.2f} · {last.ocr_input}" if last else "—"),
            ("задержка", f"{last.latency_ms:.0f} мс" if last and last.latency_ms else "—"),
            ("скорость", f"{state.rate:.2f}×"),
        ]
        x, y = PANEL.x() + 14, PANEL.y() + 22
        painter.setPen(QColor(theme.TEXT_HEADING))
        painter.drawText(int(x), int(y), "SUBTITLE VOICER · ОТЛАДКА")
        painter.setPen(QColor(theme.SUCCESS if working else theme.TEXT_MUTED))
        painter.drawText(int(PANEL.right() - 90), int(y), "● работает" if working else "● пауза")
        for index, (key, value) in enumerate(lines):
            row = y + 20 + index * 17
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(int(x), int(row), key)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(int(x + 84), int(row), value[:34])
        if state.playing:
            painter.setPen(QColor(theme.ACCENT))
            painter.drawText(int(x), int(y + 20 + 5 * 17 + 6), f"Звучит: {state.playing}"[:44])
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(int(x), int(y + 20 + 6 * 17 + 6), state.playing_text[:44])
