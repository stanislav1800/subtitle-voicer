"""Logging setup (TZ 4.14): rotating file 5 x 2 MB, optional console, uncaught exception hooks."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

LOG_FILE_NAME = "subvoicer.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 4  # current file + 4 rotated = 5 files
FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

log = logging.getLogger("subvoicer")

_HANDLER_MARK = "_subvoicer_handler"


def setup_logging(log_dir: Path, *, level: int = logging.INFO, console: bool = False) -> Path:
    """Configure the ``subvoicer`` logger; safe to call again (replaces own handlers).

    Returns the path of the log file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME
    formatter = logging.Formatter(FORMAT, DATE_FORMAT)

    for handler in list(log.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            log.removeHandler(handler)
            handler.close()

    file_handler = RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handlers: list[logging.Handler] = [file_handler]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_MARK, True)
        log.addHandler(handler)

    log.setLevel(level)
    install_exception_hooks()
    return log_file


def install_exception_hooks() -> None:
    """Route uncaught exceptions of the main and any other thread to the log."""
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_thread


def _log_uncaught(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    log.critical("Необработанное исключение", exc_info=(exc_type, exc, tb))


def _log_uncaught_thread(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is None:
        return
    name = args.thread.name if args.thread else "?"
    log.critical(
        "Необработанное исключение в потоке %s",
        name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
