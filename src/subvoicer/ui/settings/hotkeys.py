"""«Горячие клавиши»: the general hotkeys of TZ 4.12 (docs/design/Hotkeys.html)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from subvoicer.app.facade import AppFacade
from subvoicer.ui import theme
from subvoicer.ui.settings.window import Page
from subvoicer.ui.tray import pretty
from subvoicer.ui.widgets.common import Card, clear_layout, keycap, label, titled

ACTIONS = (  # key, title, hint, developer only
    ("pause", "Пауза / продолжить", "Включает и выключает автоозвучку", False),
    ("repeat", "Повторить последнюю реплику", "Если пропустили фразу", False),
    ("skip", "Пропустить текущую", "Обрывает звучащую фразу", False),
    (
        "select_read",
        "Выделить и прочитать",
        "Выделите область на кадре игры — прочитается один раз",
        False,
    ),
    ("zone_editor", "Настроить зоны", "Открывает редактор зон поверх игры", False),
    ("debug_capture", "Сохранить кадр для отладки", "Только в режиме разработчика", True),
)

MODIFIERS = (
    (Qt.KeyboardModifier.ControlModifier, "<ctrl>"),
    (Qt.KeyboardModifier.AltModifier, "<alt>"),
    (Qt.KeyboardModifier.ShiftModifier, "<shift>"),
    (Qt.KeyboardModifier.MetaModifier, "<cmd>"),
)
MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
}
NAMED_KEYS = {
    Qt.Key.Key_Space: "<space>",
    Qt.Key.Key_Tab: "<tab>",
    Qt.Key.Key_Home: "<home>",
    Qt.Key.Key_End: "<end>",
    Qt.Key.Key_Insert: "<insert>",
    Qt.Key.Key_Delete: "<delete>",
    Qt.Key.Key_PageUp: "<page_up>",
    Qt.Key.Key_PageDown: "<page_down>",
    Qt.Key.Key_Up: "<up>",
    Qt.Key.Key_Down: "<down>",
    Qt.Key.Key_Left: "<left>",
    Qt.Key.Key_Right: "<right>",
}
# letters by physical key: a Russian layout must still give "<ctrl>+<alt>+p"
LATIN = {getattr(Qt.Key, f"Key_{c}"): c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
DIGITS = {getattr(Qt.Key, f"Key_{d}"): d for d in "0123456789"}


def from_virtual_key(vk: int) -> str | None:
    """Windows virtual key -> pynput key name; the same in any keyboard layout."""
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"<f{vk - 0x70 + 1}>"
    return None


def to_pynput(key: int, modifiers: Qt.KeyboardModifier, virtual_key: int = 0) -> str | None:
    """A key press -> pynput hotkey text, or None when it is not a usable combination
    (TZ 4.12: global hotkeys need at least Ctrl, Alt or Win). With a Russian layout Qt
    reports Cyrillic keys, so the Windows virtual key decides the letter."""
    if key in MODIFIER_KEYS:
        return None
    parts = [name for flag, name in MODIFIERS if modifiers & flag]
    if not any(p in parts for p in ("<ctrl>", "<alt>", "<cmd>")):
        return None
    qt_key = Qt.Key(key)
    by_vk = from_virtual_key(virtual_key) if virtual_key else None
    if by_vk is not None:
        name = by_vk
    elif qt_key in LATIN:
        name = LATIN[qt_key]
    elif qt_key in DIGITS:
        name = DIGITS[qt_key]
    elif Qt.Key.Key_F1 <= qt_key <= Qt.Key.Key_F24:
        name = f"<f{int(qt_key) - int(Qt.Key.Key_F1) + 1}>"
    elif qt_key in NAMED_KEYS:
        name = NAMED_KEYS[qt_key]
    else:
        return None
    return "+".join([*parts, name])


class CaptureDialog(QDialog):
    """Waits for the next key combination; Esc cancels."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новое сочетание")
        self.result_hotkey: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(label(title, "windowTitle"))
        self._hint = label(
            "Нажмите новое сочетание: Ctrl, Alt или Win и клавишу. Esc — отмена.",
            "caption",
            wrap=True,
        )
        layout.addWidget(self._hint)
        self.setMinimumWidth(380)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        hotkey = to_pynput(event.key(), event.modifiers(), event.nativeVirtualKey())
        if hotkey is None:
            if event.key() not in MODIFIER_KEYS:
                self._hint.setText("Нужен Ctrl, Alt или Win вместе с буквой, цифрой или F1–F24.")
            return
        self.result_hotkey = hotkey
        self.accept()


def keycaps(hotkey: str) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    for part in pretty(hotkey).split("+"):
        layout.addWidget(keycap(part))
    return box


class HotkeysPage(Page):
    def __init__(self, facade: AppFacade) -> None:
        super().__init__(
            "Горячие клавиши", "Работают поверх игры. Нажмите «Изменить» и затем новое сочетание."
        )
        self._facade = facade
        self._card = Card()
        self.content.addWidget(self._card)
        self._error = label("", "hint", wrap=True)
        self.content.addWidget(self._error)
        note = QFrame()
        note.setObjectName("card")
        note_layout = QVBoxLayout(note)
        note_layout.addWidget(
            label(
                "Клавиши для отдельных зон (например, «Записка» — Ctrl+Alt+1) задаются в "
                "настройках зоны профиля и работают, только пока запущена эта игра. Занятое "
                "сочетание подсвечивается, сохранить его нельзя.",
                "caption",
                wrap=True,
            )
        )
        self.content.addWidget(note)
        self.finish()
        self.refresh()

    def refresh(self) -> None:
        body = self._card.body
        clear_layout(body)
        settings = self._facade.settings
        for key, title, hint, developer in ACTIONS:
            if developer and not settings.dev_mode:
                continue
            line = QWidget()
            layout = QHBoxLayout(line)
            layout.setContentsMargins(0, 4, 0, 4)
            layout.addWidget(titled(title, hint), 1)
            layout.addWidget(keycaps(settings.hotkeys[key]))
            change = QPushButton("Изменить")
            change.setAccessibleName(f"Изменить: {title}")
            change.clicked.connect(lambda _=False, k=key, t=title: self._change(k, t))
            layout.addWidget(change)
            body.addWidget(line)

    def _change(self, key: str, title: str) -> None:
        dialog = CaptureDialog(title, self)
        if not dialog.exec() or dialog.result_hotkey is None:
            return
        hotkey = dialog.result_hotkey
        conflict = self._conflict(key, hotkey)
        if conflict:
            self._error.setText(f"{pretty(hotkey)} уже занято: {conflict}. Не сохранено.")
            self._error.setStyleSheet(f"color: {theme.CHIPS['error'].text};")
            return
        self._error.setText("")
        hotkeys = {**self._facade.settings.hotkeys, key: hotkey}
        self._facade.apply_settings(self._facade.settings.model_copy(update={"hotkeys": hotkeys}))
        self.refresh()

    def _conflict(self, key: str, hotkey: str) -> str | None:
        titles = {k: t for k, t, _, _ in ACTIONS}
        for other, combo in self._facade.settings.hotkeys.items():
            if other != key and combo == hotkey:
                return titles.get(other, other)
        return self._facade.zone_hotkeys().get(hotkey)
