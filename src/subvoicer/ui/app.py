"""GUI entry point: ``uv run python -m subvoicer`` (TZ 4.15)."""

from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from subvoicer.app import runtime
from subvoicer.app.facade import AppFacade
from subvoicer.app.log import setup_logging
from subvoicer.config.paths import AppPaths
from subvoicer.core.events import Event, GameActivated, GameClosed, LayoutMissing, SettingsApplied
from subvoicer.core.models import HistoryEntry
from subvoicer.ui import theme
from subvoicer.ui.bridge import Bridge
from subvoicer.ui.settings.general import GeneralPage
from subvoicer.ui.settings.hotkeys import HotkeysPage
from subvoicer.ui.settings.window import Page, SettingsWindow
from subvoicer.ui.tray import Tray

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
        self.loader = Loader(facade)
        self.loader.done.connect(self._on_loaded)

    def _build_pages(self) -> None:
        self.general = GeneralPage(self.facade)
        self.window.add_page("general", "Общие", "sliders-horizontal", self.general)
        profiles = Page("Профили и голоса")
        profiles.finish()
        self.window.add_page("profiles", "Профили и голоса", "users", profiles)
        self.hotkeys = HotkeysPage(self.facade)
        self.window.add_page("hotkeys", "Горячие клавиши", "keyboard", self.hotkeys)

    def run(self) -> None:
        self.tray.show()
        self.loader.start()

    def open_line(self, entry: HistoryEntry) -> None:
        log.info("Реплика %d: %s", entry.id, entry.text)

    def open_zone_editor(self) -> None:
        log.info("Редактор зон появится в части 5 этапа 6")

    def quit(self) -> None:
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
        if isinstance(event, SettingsApplied):
            self.tray.refresh()
            self.window.refresh_status()
            self.general.sync()

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
    setup_logging(paths.logs_dir, level=logging.INFO, console=False)
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
