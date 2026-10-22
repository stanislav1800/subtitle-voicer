"""The wheel over a field scrolls the page; it changes the field only once it has the focus."""

import sys

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from subvoicer.ui.widgets.wheel import install_wheel_guard


@pytest.fixture(scope="module")
def qt() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    assert isinstance(app, QApplication)
    install_wheel_guard(app)
    return app


def wheel_down(widget: QWidget) -> None:
    centre = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(
        centre,
        QPointF(widget.mapToGlobal(QPoint(int(centre.x()), int(centre.y())))),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_wheel_over_an_unfocused_drop_down_scrolls_the_page(qt: QApplication) -> None:
    page = QScrollArea()
    page.setWidgetResizable(True)
    inner = QWidget()
    layout = QVBoxLayout(inner)
    box = QComboBox()
    box.addItems(["первый", "второй", "третий"])
    layout.addWidget(box)
    for i in range(60):
        layout.addWidget(QLabel(f"строка {i}"))
    page.setWidget(inner)
    page.resize(300, 200)
    page.show()
    qt.processEvents()
    page.setFocus()  # the first field of a shown window may take the focus by itself
    qt.processEvents()
    assert not box.hasFocus()
    assert box.focusPolicy() == Qt.FocusPolicy.StrongFocus  # the wheel gives no focus
    bar = page.verticalScrollBar()
    wheel_down(page.viewport())  # an empty spot of the page: the page hands it to its bar
    qt.processEvents()
    assert bar.value() > 0  # it used to go round between the page and the bar and crash
    wheel_down(bar)
    qt.processEvents()
    scrolled = bar.value()
    bar.setValue(0)
    wheel_down(box)
    qt.processEvents()
    assert box.currentIndex() == 0 and bar.value() > 0 and scrolled > 0
    box.setFocus()
    page.activateWindow()
    qt.processEvents()
    if box.hasFocus():  # a window without focus on a CI desktop cannot take it
        wheel_down(box)
        assert box.currentIndex() == 1
    page.close()
