from PySide6.QtCore import Qt

from subvoicer.ui.settings.hotkeys import to_pynput

CTRL_ALT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier


def test_combinations() -> None:
    assert to_pynput(Qt.Key.Key_P, CTRL_ALT) == "<ctrl>+<alt>+p"
    assert to_pynput(Qt.Key.Key_1, Qt.KeyboardModifier.AltModifier) == "<alt>+1"
    assert to_pynput(Qt.Key.Key_F5, Qt.KeyboardModifier.ControlModifier) == "<ctrl>+<f5>"


def test_not_usable() -> None:
    assert to_pynput(Qt.Key.Key_P, Qt.KeyboardModifier.NoModifier) is None
    assert to_pynput(Qt.Key.Key_P, Qt.KeyboardModifier.ShiftModifier) is None  # typing
    assert to_pynput(Qt.Key.Key_Control, CTRL_ALT) is None  # only modifiers so far
