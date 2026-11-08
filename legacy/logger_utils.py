import os
import sys
import atexit
import queue
import tempfile
import logging
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from typing import Optional
from version import APP_VERSION


try:
    from utils import documents_dir
except Exception:
    def documents_dir():
        return os.path.join(os.path.expanduser("~"), "Documents")


APP_NAME = "DIMCreator"
ENV_LEVEL = os.getenv("DIMCREATOR_LOG_LEVEL", "INFO").upper()
ENABLE_CONSOLE = os.getenv("DIMCREATOR_CONSOLE", "0") == "1"

logger: logging.Logger
queue_listener: Optional[QueueListener] = None
_main_log_path = None
_err_log_path = None


class AppContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.app = APP_NAME
        record.version = APP_VERSION
        return True


def _ensure_logs_dir() -> str:
    candidates = [
        os.path.join(documents_dir(), APP_NAME, "Logs"),
        os.path.join(os.path.expanduser("~"), APP_NAME, "Logs"),
        os.path.join(tempfile.gettempdir(), APP_NAME, "Logs"),
    ]
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, ".write_test")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_file)
            return path
        except Exception:
            continue
    path = os.path.join(tempfile.gettempdir(), APP_NAME + "_Logs")
    os.makedirs(path, exist_ok=True)
    return path


def _build_formatters():
    file_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(app)s v%(version)s | %(name)s:%(lineno)d | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_fmt = logging.Formatter(
        fmt="%(levelname)s | %(name)s:%(lineno)d | %(message)s"
    )
    return file_fmt, console_fmt


def _start_queue_listener(handlers):
    q = queue.Queue(-1)
    qh = QueueHandler(q)
    listener = QueueListener(q, *handlers, respect_handler_level=True)
    return qh, listener


def _make_file_handlers(log_dir: str):
    global _main_log_path, _err_log_path

    _main_log_path = os.path.join(log_dir, f"{APP_NAME}.log")
    _err_log_path = os.path.join(log_dir, f"{APP_NAME}.error.log")

    main_fh = RotatingFileHandler(
        _main_log_path, maxBytes=5_000_000, backupCount=7, encoding="utf-8"
    )
    main_fh.setLevel(getattr(logging, ENV_LEVEL, logging.INFO))

    err_fh = RotatingFileHandler(
        _err_log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    err_fh.setLevel(logging.ERROR)

    return main_fh, err_fh


def _make_console_handler():
    ch = logging.StreamHandler(stream=sys.stderr)
    ch.setLevel(getattr(logging, ENV_LEVEL, logging.INFO))
    return ch


def init_logging(level: str = ENV_LEVEL):
    global queue_listener

    log_dir = _ensure_logs_dir()
    file_fmt, console_fmt = _build_formatters()
    main_fh, err_fh = _make_file_handlers(log_dir)

    main_fh.addFilter(AppContextFilter())
    err_fh.addFilter(AppContextFilter())
    main_fh.setFormatter(file_fmt)
    err_fh.setFormatter(file_fmt)

    handlers = [main_fh, err_fh]
    if ENABLE_CONSOLE:
        ch = _make_console_handler()
        ch.addFilter(AppContextFilter())
        ch.setFormatter(console_fmt)
        handlers.append(ch)

    qh, listener = _start_queue_listener(handlers)

    base = logging.getLogger(APP_NAME)
    base.setLevel(getattr(logging, level.upper(), logging.INFO))
    base.propagate = False

    base.handlers.clear()
    base.addHandler(qh)

    global queue_listener
    if queue_listener:
        try:
            queue_listener.stop()
        except Exception:
            pass

    listener.start()
    queue_listener = listener

    globals()["logger"] = base

    _install_excepthook()

    atexit.register(_shutdown_logging)

    base.info("Logging initialized at level %s", level.upper())
    base.info("Log file: %s", get_log_file_path())
    base.info("Error log: %s", get_error_log_file_path())


def _shutdown_logging():
    try:
        if queue_listener:
            queue_listener.stop()
    finally:
        logging.shutdown()


def set_level(level: str):
    lvl = getattr(logging, level.upper(), logging.INFO)
    log = logging.getLogger(APP_NAME)
    log.setLevel(lvl)
    log.info("Logging level changed to %s", level.upper())


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if not name:
        return logging.getLogger(APP_NAME)
    return logging.getLogger(f"{APP_NAME}.{name}")


def get_log_file_path() -> Optional[str]:
    return _main_log_path


def get_error_log_file_path() -> Optional[str]:
    return _err_log_path


def _install_excepthook():
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        log = logging.getLogger(APP_NAME)
        log.exception("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
        except Exception:
            pass

    sys.excepthook = handle_exception


init_logging(ENV_LEVEL)
log = logging.getLogger(APP_NAME)
log.info("%s started", APP_NAME)
