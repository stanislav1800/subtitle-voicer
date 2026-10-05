"""GUI entry point: ``uv run python -m subvoicer`` (TZ 4.15)."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from subvoicer.app import runtime, zone_tools
from subvoicer.app.facade import AppFacade
from subvoicer.app.log import setup_logging
from subvoicer.config.paths import AppPaths
from subvoicer.core.events import (
    DebugCaptured,
    Event,
    GameActivated,
    GameClosed,
    LayoutMissing,
    ProfileBound,
    ProfileOffered,
    SettingsApplied,
)
from subvoicer.core.models import HistoryEntry, WindowRef
from subvoicer.ui import theme
from subvoicer.ui.bridge import Bridge
from subvoicer.ui.line_window import LineWindow
from subvoicer.ui.notifications import Notification, Notifier, mono
from subvoicer.ui.overlay import Overlay
from subvoicer.ui.select_area import AreaSelector, Fractions
from subvoicer.ui.settings.general import GeneralPage
from subvoicer.ui.settings.history import HistoryPage
from subvoicer.ui.settings.hotkeys import HotkeysPage
from subvoicer.ui.settings.profiles import ProfilesPage
from subvoicer.ui.settings.window import SettingsWindow
from subvoicer.ui.tray import Tray, pretty
from subvoicer.ui.zone_editor.editor import ZoneEditor

log = logging.getLogger("subvoicer.ui")

SERVER_NAME = "SubtitleVoicer.Show"  # a second copy asks the first to show its window
STATUS_EVENTS = (GameActivated, GameClosed, LayoutMissing)


def tell_first_copy() -> None:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(1000):
        socket.write(b"show")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()


class HotkeySignals(QObject):
    """Hotkeys fire in the pynput thread; windows open in the Qt one."""

    zone_editor = Signal()
    select_read = Signal()


class Loader(QObject):
    """Loads OCR and TTS off the GUI thread."""

    done = Signal(str)  # "" on success, else the error

    def __init__(self, facade: AppFacade) -> None:
        super().__init__()
        self._facade = facade

    def start(self) -> None:
        threading.Thread(target=self._run, name="Loader", daemon=True).start()

    def _run(self) -> None:
        try:
            self._facade.start()
        except Exception as exc:
            log.exception("Не удалось запустить озвучку")
            self.done.emit(str(exc))
            return
        self.done.emit("")


class Application:
    def __init__(self, qt: QApplication, facade: AppFacade) -> None:
        self.qt = qt
        self.facade = facade
        self.bridge = Bridge(facade)
        self.window = SettingsWindow(facade)
        self._build_pages()
        self.tray = Tray(
            facade,
            open_settings=self.window.bring_up,
            open_line=self.open_line,
            open_zone_editor=self.open_zone_editor,
            quit_app=self.quit,
        )
        self.bridge.event.connect(self._on_event)
        self.server = QLocalServer()
        QLocalServer.removeServer(SERVER_NAME)
        self.server.newConnection.connect(self._on_second_copy)
        self.server.listen(SERVER_NAME)
        self.notifier = Notifier()
        self._missing_shown: set[tuple[str, str]] = set()
        self.overlay = Overlay(facade)
        self.line_window: LineWindow | None = None
        self.editor: ZoneEditor | None = None
        self.hotkey_signals = HotkeySignals()
        self.hotkey_signals.zone_editor.connect(self.open_zone_editor)
        facade.set_hotkey_action("zone_editor", self.hotkey_signals.zone_editor.emit)
        self.hotkey_signals.select_read.connect(self.select_and_read)
        facade.set_hotkey_action("select_read", self.hotkey_signals.select_read.emit)
        self.selector: AreaSelector | None = None
        self.loader = Loader(facade)
        self.loader.done.connect(self._on_loaded)

    def _build_pages(self) -> None:
        self.general = GeneralPage(self.facade)
        self.window.add_page("general", "Общие", "sliders-horizontal", self.general)
        self.profiles = ProfilesPage(self.facade, self.open_profile_editor)
        self.window.add_page("profiles", "Профили и голоса", "users", self.profiles)
        self.hotkeys = HotkeysPage(self.facade)
        self.window.add_page("hotkeys", "Горячие клавиши", "keyboard", self.hotkeys)
        self.history = HistoryPage(self.facade)
        self.window.add_page("history", "История", "history", self.history)
        self.window.set_page_visible("history", self.facade.settings.dev_mode)

    def run(self) -> None:
        self.tray.show()
        self.loader.start()

    def open_line(self, entry: HistoryEntry) -> None:
        if self.line_window is None:
            self.line_window = LineWindow(self.facade)
        current = self.facade.history.get(entry.id) or entry  # voice may be known by now
        self.line_window.show_entry(current)

    def open_profile_editor(self, path: Path, mode: str) -> None:
        """«Настроить в игре» / «По скриншоту» from the profile tab."""
        if mode == "game":
            status = self.facade.status()
            if status.profile_path != str(path):
                self.tray.showMessage(
                    "Subtitle Voicer", "Запустите эту игру — редактор откроется поверх неё"
                )
                return
            self.open_zone_editor()
            return
        start = str(self.facade.paths.captures_dir) if self.facade.settings.dev_mode else ""
        name, _ = QFileDialog.getOpenFileName(
            self.window, "Полный кадр игры", start, "Изображения (*.png *.jpg *.jpeg *.bmp)"
        )
        if name:
            self.open_screenshot(path, Path(name))

    def open_screenshot(self, path: Path, name: Path) -> None:
        """The zone editor «по скриншоту» of profile ``path`` on the picture ``name``."""
        image = zone_tools.load_image(name)
        if image is None:
            QMessageBox.warning(self.window, "Скриншот", "Не удалось открыть картинку")
            return
        height, width = image.shape[:2]
        warnings = []
        if not zone_tools.is_known_aspect(width, height):
            warnings.append(
                f"Картинка {width}×{height} — не 16:9, 16:10, 21:9, 32:9 или 4:3. Нужен полный "
                "кадр игры, иначе зоны могут съехать."
            )
        if name.suffix.lower() in (".jpg", ".jpeg"):
            warnings.append(
                "JPEG: цвета могут быть неточными (сжатие, кадр из видео) — проверьте маску в игре."
            )
        self._show_editor(ZoneEditor(self.facade, path, image, "screenshot", warnings))

    def open_zone_editor(self) -> None:
        """Ctrl+Alt+Z or «Настроить зоны»: a frozen frame of the active game."""
        status = self.facade.status()
        if status.profile_path is None:
            self.tray.showMessage("Subtitle Voicer", "Нет активной игры с профилем")
            return
        frame = self.facade.grab_game_frame()
        if frame is None:
            self.tray.showMessage("Subtitle Voicer", "Не удалось получить кадр игры")
            return
        self._show_editor(ZoneEditor(self.facade, Path(status.profile_path), frame, "game"))

    def select_and_read(self) -> None:
        """Ctrl+Alt+A: select an area on the frozen game frame, it is read once (TZ 4.12)."""
        status = self.facade.status()
        if status.window is None or status.profile_path is None:
            return  # available only with an active game
        frame = self.facade.game_frame()
        if frame is None:
            self.tray.showMessage("Subtitle Voicer", "Не удалось получить кадр игры")
            return
        if self.selector is not None:
            self.selector.close()

        def read(rect: Fractions) -> None:
            threading.Thread(
                target=self.facade.read_area, args=(frame, rect), name="ReadArea", daemon=True
            ).start()

        hwnd = status.window.hwnd
        self.selector = AreaSelector(
            frame, runtime.client_rect(hwnd), self.facade.game_monitor(), read
        )
        self.selector.destroyed.connect(lambda: setattr(self, "selector", None))
        self.selector.open()

    def _show_editor(self, editor: ZoneEditor) -> None:
        if self.editor is not None:
            self.editor.close()
        self.editor = editor
        editor.destroyed.connect(lambda: setattr(self, "editor", None))
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.open()

    def quit(self) -> None:
        self.overlay.hide()
        self.tray.hide()
        self.facade.stop()  # restores the game's volume
        self.qt.quit()

    def _on_loaded(self, error: str) -> None:
        if error:
            self.tray.showMessage("Subtitle Voicer", f"Озвучка не запустилась: {error}")
        self.tray.refresh()
        self.window.refresh_status()

    def _on_event(self, event: Event) -> None:
        if isinstance(event, STATUS_EVENTS):
            self.tray.refresh()
            self.window.refresh_status()
        match event:
            case ProfileOffered(window=window):
                self.tray.offer(window, self._create_profile)
                self.notifier.show(
                    Notification(
                        "Похоже, это новая игра",
                        f"{mono(window.exe)} — создать профиль, чтобы озвучивать её субтитры? "
                        "Если не успели нажать — пункт есть в меню трея.",
                        ("Создать профиль", lambda: self._create_profile(window)),
                        ("Не спрашивать", lambda: self.facade.never_ask(window.exe)),
                        timeout_ms=20_000,
                    )
                )
            case LayoutMissing(profile_path=path, title=title, aspect=aspect):
                if (path, aspect) in self._missing_shown:
                    return  # once per game and layout, not on every profile re-read
                self._missing_shown.add((path, aspect))
                key = pretty(self.facade.settings.hotkeys["zone_editor"])
                self.notifier.show(
                    Notification(
                        "Настройте зоны для этой игры",
                        f"{title or 'Игра'}: нет зон для {aspect}. В игре нажмите {key} — "
                        "редактор откроется поверх неё.",
                        ("Настроить зоны", self.open_zone_editor),
                        None,
                        timeout_ms=20_000,
                    )
                )
            case ProfileBound(title=title):
                self.notifier.show(Notification("Игра привязана", f"{title} — профиль готов."))
            case DebugCaptured(folder=folder):
                profile = self.facade.status().profile_path
                frame = Path(folder) / "frame.png"
                action = (
                    ("Открыть в редакторе", lambda: self.open_screenshot(Path(profile), frame))
                    if profile is not None
                    else None
                )
                self.notifier.show(Notification("Кадр сохранён", mono(folder), action))
            case _:
                pass
        if isinstance(event, SettingsApplied):
            self.tray.refresh()
            self.window.refresh_status()
            self.general.sync()
            self.window.set_page_visible("history", self.facade.settings.dev_mode)
            self.hotkeys.refresh()
        if isinstance(event, (*STATUS_EVENTS, SettingsApplied)):
            self.overlay.sync()

    def _create_profile(self, window: WindowRef) -> None:
        try:
            stored = self.facade.create_profile_for(window)
        except (OSError, ValueError, FileExistsError) as exc:
            self.notifier.show(Notification("Профиль не создан", str(exc)))
            return
        log.info("Создан профиль %s", stored.path.name)
        self.tray.offer(None, self._create_profile)
        self.profiles.refresh()

    def _on_second_copy(self) -> None:
        connection = self.server.nextPendingConnection()
        if connection is not None:
            connection.close()
        self.window.bring_up()


def main() -> int:
    runtime.prepare_process()
    qt = QApplication(sys.argv)
    qt.setApplicationName("Subtitle Voicer")
    qt.setQuitOnLastWindowClosed(False)
    instance = runtime.acquire_single_instance()
    if instance is None:
        tell_first_copy()
        return 0
    paths = AppPaths.default()
    log_file = setup_logging(paths.logs_dir, level=logging.INFO, console=False)
    log.info("Subtitle Voicer запущен, лог: %s", log_file)
    theme.apply(qt)
    facade = AppFacade(paths)
    app = Application(qt, facade)
    app.run()
    QTimer.singleShot(0, app.window.bring_up)
    try:
        return qt.exec()
    finally:
        facade.stop()
        runtime.release_single_instance(instance)
