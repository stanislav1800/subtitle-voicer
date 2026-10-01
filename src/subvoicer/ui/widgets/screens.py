"""Which Qt screen shows a Windows monitor (Qt 6 names screens by model, not by device)."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QScreen

PhysicalRect = tuple[int, int, int, int]  # left, top, right, bottom in physical pixels


def screen_for(monitor: PhysicalRect | None) -> QScreen | None:
    """The screen whose physical size matches the monitor, nearest by position."""
    screens = QGuiApplication.screens()
    if monitor is None or not screens:
        return QGuiApplication.primaryScreen()
    left, top, right, bottom = monitor
    width, height = right - left, bottom - top

    def score(screen: QScreen) -> tuple[int, float]:
        ratio = screen.devicePixelRatio()
        geometry = screen.geometry()
        size_off = abs(round(geometry.width() * ratio) - width) + abs(
            round(geometry.height() * ratio) - height
        )
        origin_off = abs(geometry.x() * ratio - left) + abs(geometry.y() * ratio - top)
        return size_off, origin_off

    return min(screens, key=score)
