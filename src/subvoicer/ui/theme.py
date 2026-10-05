"""Colours, fonts, icons and the application style sheet (docs/UI.md).

Every colour of the interface is a token here; widgets never use colours of their own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent
FONTS_DIR = UI_DIR / "fonts"
ICONS_DIR = UI_DIR / "icons"

# ---------------------------------------------------------------- colours (UI.md, section 2)

BG_DEEP = "#0e141b"
BG_TITLEBAR = "#0b1016"
BG_WINDOW = "#171d25"
BG_SIDEBAR = "#121820"
BG_CARD = "#1b232d"
BG_CARD_HEADER = "#1f2833"
BG_ACTIVE = "#1f2a37"
BG_CONTROL = "#263241"
BORDER = "#263241"
BORDER_CONTROL = "#2e3b4b"
DIVIDER = "#222c38"
TEXT = "#e6edf3"
TEXT_SECONDARY = "#9fb0c2"
TEXT_MUTED = "#7d8fa3"
TEXT_HEADING = "#cfd9e3"
ACCENT = "#3fb4ff"
ACCENT_HOVER = "#8fd3ff"
ON_ACCENT = "#0b1520"
SUCCESS = "#5fd38d"


@dataclass(frozen=True, slots=True)
class ChipStyle:
    background: str
    text: str


CHIPS = {  # UI.md: chips are a background and a text of one hue
    "info": ChipStyle("#1d2f40", "#8fd3ff"),  # auto / information
    "success": ChipStyle("#243a2d", "#8fe0ad"),  # manual / success
    "import": ChipStyle("#2c2a44", "#c5b8ff"),  # from a list
    "warning": ChipStyle("#3a3020", "#f0c674"),  # attention / not configured
    "error": ChipStyle("#3a2426", "#ffab9e"),  # error / dropped
}

ZONE_COLORS = ("#3fb4ff", "#c5b8ff", "#f0c674", "#8fe0ad", "#ffab9e", "#8fd3ff")
ZONE_DIALOG, ZONE_HOTKEY, ZONE_LOW = ZONE_COLORS[0], ZONE_COLORS[1], ZONE_COLORS[2]

# ---------------------------------------------------------------- fonts (UI.md, section 3)

HEADING_FAMILY = "Exo 2"
BODY_FAMILY = "Golos Text"
MONO_FAMILY = "JetBrains Mono"
FALLBACK_FAMILY = "Segoe UI"
FONT_FILES = ("Exo2[wght].ttf", "GolosText[wght].ttf", "JetBrainsMono[wght].ttf")

_loaded_families: set[str] = set()


def load_fonts() -> None:
    """Register the bundled fonts; a font that fails falls back to Segoe UI (UI.md)."""
    for name in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(FONTS_DIR / name))
        if font_id < 0:
            log.warning("Шрифт %s не загружен — будет %s", name, FALLBACK_FAMILY)
            continue
        _loaded_families.update(QFontDatabase.applicationFontFamilies(font_id))


def family(name: str) -> str:
    return name if name in _loaded_families else FALLBACK_FAMILY


def font(name: str, pixel_size: int, weight: int = 400) -> QFont:
    result = QFont(family(name))
    result.setPixelSize(pixel_size)
    result.setWeight(QFont.Weight(weight))
    return result


def mono(pixel_size: int = 12) -> QFont:
    return font(MONO_FAMILY, pixel_size)


# ---------------------------------------------------------------- icons


@cache
def _svg(name: str) -> str:
    return (ICONS_DIR / f"{name}.svg").read_text(encoding="utf-8")


def icon(name: str, color: str = TEXT_SECONDARY, size: int = 18) -> QIcon:
    """A Lucide icon recoloured to ``color`` (the SVGs draw with ``currentColor``)."""
    return QIcon(pixmap(name, color, size))


def pixmap(name: str, color: str = TEXT_SECONDARY, size: int = 18) -> QPixmap:
    svg = _svg(name).replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    ratio = 2.0  # sharp on high-DPI screens
    result = QPixmap(QSize(round(size * ratio), round(size * ratio)))
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    renderer.render(painter)
    painter.end()
    result.setDevicePixelRatio(ratio)
    return result


def app_icon(color: str = ACCENT) -> QIcon:
    """The tray and window icon: the audio-lines sign on an accent square."""
    size = 64
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 14, 14)
    sign = pixmap("audio-lines", ON_ACCENT, 40)
    painter.drawPixmap(12, 12, 40, 40, sign)
    painter.end()
    return QIcon(image)


# ---------------------------------------------------------------- style sheet


def style_sheet() -> str:
    body, heading, monospace = family(BODY_FAMILY), family(HEADING_FAMILY), family(MONO_FAMILY)
    return f"""
* {{ font-family: "{body}"; font-size: 14px; color: {TEXT}; }}
QWidget#window, QDialog, QMainWindow {{ background: {BG_WINDOW}; }}
QToolTip {{ background: {BG_WINDOW}; color: {TEXT}; border: 1px solid {BORDER_CONTROL};
    border-radius: 6px; padding: 4px 8px; }}

QLabel#pageTitle {{ font-family: "{heading}"; font-size: 26px; font-weight: 700; }}
QLabel#windowTitle {{ font-family: "{heading}"; font-size: 18px; font-weight: 700; }}
QLabel#sectionTitle {{ font-family: "{heading}"; font-size: 13px; font-weight: 600;
    color: {TEXT_HEADING}; letter-spacing: 1px; }}
QLabel#hint {{ font-size: 12px; color: {TEXT_MUTED}; }}
QLabel#caption {{ font-size: 13px; color: {TEXT_SECONDARY}; }}
QLabel#value {{ color: {ACCENT}; font-weight: 600; }}
QLabel#mono {{ font-family: "{monospace}"; font-size: 12px; color: {TEXT_SECONDARY}; }}
QLabel#keycap {{ font-family: "{monospace}"; font-size: 12px; background: {BG_CONTROL};
    border: 1px solid {BORDER_CONTROL}; border-bottom: 3px solid {BORDER_CONTROL};
    border-radius: 6px; padding: 3px 8px; }}

QFrame#sidebar {{ background: {BG_SIDEBAR}; border-right: 1px solid {DIVIDER}; }}
QFrame#card {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#statusCard {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}

QPushButton {{ background: {BG_CONTROL}; border: 1px solid {BORDER_CONTROL}; border-radius: 8px;
    padding: 0 14px; min-height: 34px; font-size: 13px; }}
QPushButton:hover {{ border-color: {TEXT_MUTED}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; }}
QPushButton:checked {{ background: {BG_ACTIVE}; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ON_ACCENT}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QProgressBar {{ background: {BG_CONTROL}; border: none; border-radius: 4px;
    max-height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QPushButton#iconButton {{ min-height: 32px; min-width: 32px; max-width: 32px; padding: 0;
    font-size: 16px;
    border-radius: 7px; }}
QPushButton#iconButton::menu-indicator {{ image: none; width: 0; }}
QPushButton#nav {{ background: transparent; border: none; text-align: left; padding: 0 12px;
    min-height: 38px; color: {TEXT_SECONDARY}; border-radius: 8px; }}
QPushButton#nav:checked {{ background: {BG_ACTIVE}; color: {TEXT}; font-weight: 600; }}
QPushButton#nav:hover:!checked {{ color: {TEXT}; }}
QPushButton#segment {{ background: transparent; border: none; border-radius: 6px;
    min-height: 28px; padding: 0 12px; color: {TEXT_SECONDARY}; }}
QPushButton#segment:checked {{ background: {BG_CONTROL}; color: {TEXT}; }}
QFrame#segments {{ background: {BG_SIDEBAR}; border: 1px solid {BORDER_CONTROL};
    border-radius: 8px; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {BG_SIDEBAR}; border: 1px solid {BORDER_CONTROL}; border-radius: 8px;
    padding: 0 10px; min-height: 34px; selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT}; }}
QPlainTextEdit, QTextEdit {{ padding: 6px 10px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus {{ border: 2px solid {ACCENT}; }}
QLineEdit#mono {{ font-family: "{monospace}"; font-size: 12px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{ background: {BG_WINDOW}; border: 1px solid {BORDER_CONTROL};
    selection-background-color: {BG_ACTIVE}; outline: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER_CONTROL}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; }}

QTableView, QListWidget, QTreeView {{ background: {BG_CARD}; border: 1px solid {BORDER};
    border-radius: 10px; gridline-color: {DIVIDER}; outline: none; }}
QTableView::item, QListWidget::item {{ border-bottom: 1px solid {DIVIDER}; padding: 6px; }}
QTableView::item:selected, QListWidget::item:selected {{ background: {BG_ACTIVE};
    color: {TEXT}; }}
QHeaderView::section {{ background: {BG_CARD_HEADER}; color: {TEXT_SECONDARY}; border: none;
    border-bottom: 1px solid {DIVIDER}; padding: 8px; font-size: 11px; font-weight: 600; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_CONTROL}; border-radius: 4px;
    min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_CONTROL}; border-radius: 4px; }}

QMenu {{ background: {BG_WINDOW}; border: 1px solid {BORDER_CONTROL}; border-radius: 10px;
    padding: 6px; }}
QMenu::item {{ padding: 9px 28px 9px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {BG_ACTIVE}; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 1px; background: {DIVIDER}; margin: 6px 4px; }}

QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER_CONTROL};
    border-radius: 4px; background: {BG_SIDEBAR}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
"""


def chip_style(kind: str) -> str:
    chip = CHIPS[kind]
    return (
        f"background: {chip.background}; color: {chip.text}; border-radius: 10px;"
        " padding: 2px 10px; font-size: 12px;"
    )


def apply(app: QApplication) -> None:
    load_fonts()
    app.setFont(font(BODY_FAMILY, 14))
    app.setStyleSheet(style_sheet())
    app.setWindowIcon(app_icon())
