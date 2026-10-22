"""Small building blocks of the settings screens (docs/UI.md, section 5)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from subvoicer.ui import theme
from subvoicer.ui.widgets.toggle import ToggleSwitch


def clear_layout(layout: QLayout) -> None:
    """Remove and destroy every widget of ``layout`` now (``deleteLater`` alone leaves them
    painted until the event loop runs)."""
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def combo(items: Sequence[tuple[str, object]], value: object, name: str) -> QComboBox:
    """A drop-down that does not grow with its longest item."""
    box = QComboBox()
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    box.setMinimumContentsLength(10)
    box.setAccessibleName(name)
    for index, (text, data) in enumerate(items):
        box.addItem(text, data)
        box.setItemData(index, text, Qt.ItemDataRole.ToolTipRole)
    box.setCurrentIndex(max(box.findData(value), 0))
    # the field may be narrow, the open list is as wide as its longest item
    view = box.view()
    view.setMinimumWidth(view.sizeHintForColumn(0) + 2 * view.frameWidth() + 24)
    box.setToolTip(box.currentText())
    box.currentTextChanged.connect(box.setToolTip)
    return box


def label(text: str, name: str = "", *, wrap: bool = False) -> QLabel:
    result = QLabel(text)
    if name:
        result.setObjectName(name)
    result.setWordWrap(wrap)
    return result


class Card(QFrame):
    """A section: ``bg_card`` frame with an upper-case title."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(12)
        if title:
            self.body.addWidget(label(title.upper(), "sectionTitle"))


def chip(text: str, kind: str = "info") -> QLabel:
    result = QLabel(text)
    result.setStyleSheet(theme.chip_style(kind))
    result.setAlignment(Qt.AlignmentFlag.AlignCenter)
    result.setFixedHeight(24)
    result.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return result


def keycap(text: str) -> QLabel:
    return label(text, "keycap")


def icon_button(
    icon_name: str, tooltip: str, on_click: Callable[[], None] | None = None
) -> QPushButton:
    button = QPushButton()
    button.setObjectName("iconButton")
    button.setIcon(theme.icon(icon_name))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    if on_click is not None:
        button.clicked.connect(lambda _checked=False: on_click())  # not the checked flag
    return button


def primary(text: str, on_click: Callable[[], None] | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primary")
    if on_click is not None:
        button.clicked.connect(lambda _checked=False: on_click())  # not the checked flag
    return button


def titled(title: str, hint: str = "") -> QWidget:
    """A setting's title with a muted hint under it."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(label(title, wrap=True))
    if hint:
        layout.addWidget(label(hint, "hint", wrap=True))
    return box


def row(left: QWidget, right: QWidget) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(left, 1)
    if isinstance(right, QComboBox):  # a drop-down takes half the row: its text is readable
        layout.addWidget(right, 1, Qt.AlignmentFlag.AlignVCenter)
    else:
        layout.addWidget(right, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return box


def stacked(top: QWidget, field: QWidget) -> QWidget:
    """A title over a field as wide as the card: a drop-down in a narrow column stays
    readable."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(top)
    layout.addWidget(field)
    return box


def toggle_row(title: str, hint: str, checked: bool) -> tuple[QWidget, ToggleSwitch]:
    switch = ToggleSwitch(checked=checked)
    switch.setAccessibleName(title)
    return row(titled(title, hint), switch), switch


class SliderRow(QWidget):
    """Title, value in accent on the right, slider under them. Works in integer steps."""

    changed = Signal(float)

    def __init__(
        self,
        title: str,
        minimum: float,
        maximum: float,
        step: float,
        value: float,
        fmt: Callable[[float], str],
    ) -> None:
        super().__init__()
        self._minimum, self._step, self._fmt = minimum, step, fmt
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._value = label("", "value")
        layout.addWidget(row(label(title, wrap=True), self._value))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName(title)
        self.slider.setRange(0, round((maximum - minimum) / step))
        self.slider.setValue(round((value - minimum) / step))
        self.slider.valueChanged.connect(self._on_value)
        layout.addWidget(self.slider)
        self._on_value(self.slider.value(), emit=False)

    def value(self) -> float:
        return round(self._minimum + self.slider.value() * self._step, 6)

    def _on_value(self, _position: int, emit: bool = True) -> None:
        self._value.setText(self._fmt(self.value()))
        if emit:
            self.changed.emit(self.value())


class Segmented(QFrame):
    """Mutually exclusive checkable buttons in a frame."""

    changed = Signal(str)

    def __init__(self, options: Sequence[tuple[str, str]], value: str) -> None:
        super().__init__()
        self.setObjectName("segments")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in options:
            button = QPushButton(text)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setChecked(key == value)
            button.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(button)
            self._buttons[key] = button
            layout.addWidget(button)

    def value(self) -> str:
        return next((k for k, b in self._buttons.items() if b.isChecked()), "")

    def set_value(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)


class ElidedLabel(QLabel):
    """One line that shortens itself with «…» instead of widening the window — in the middle
    for paths, at the end for names; the full text is in the tooltip."""

    def __init__(
        self, text: str, name: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle
    ) -> None:
        super().__init__()
        if name:
            self.setObjectName(name)
        self._mode = mode
        self._full = text
        self.setToolTip(text)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update()

    def _update(self) -> None:
        metrics = self.fontMetrics()
        self.setText(metrics.elidedText(self._full, self._mode, max(self.width(), 40)))
