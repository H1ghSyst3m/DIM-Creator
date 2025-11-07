import os
import sys
import subprocess
from pathlib import Path
from contextlib import contextmanager
from PySide6.QtCore import QStandardPaths, Qt
from qfluentwidgets import InfoBar, InfoBarPosition


def resource_path(relative_path: str) -> str:
    p = Path(relative_path)
    if p.is_absolute():
        return str(p)

    if hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)
    elif getattr(sys, "frozen", False):
        base_path = Path(sys.executable).resolve().parent
    else:
        base_path = Path(__file__).resolve().parent

    return str(base_path / p)


def documents_dir():
    p = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    return p or os.path.join(os.path.expanduser('~'), 'Documents')


def downloads_dir():
    p = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
    return p or os.path.join(os.path.expanduser('~'), 'Downloads')


DOC_MAIN_DIR = os.path.join(documents_dir(), "DIMCreator")
os.makedirs(DOC_MAIN_DIR, exist_ok=True)


@contextmanager
def suppress_cmd_window():
    if os.name != "nt":
        yield
        return

    original_popen = subprocess.Popen

    si_hidden = subprocess.STARTUPINFO()
    si_hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si_hidden.wShowWindow = subprocess.SW_HIDE

    try:
        def patched_popen(*args, **kwargs):
            flags = kwargs.get("creationflags", 0)
            try:
                C_NEW_CON = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
                C_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if not (flags & (C_NEW_CON | DETACHED)):
                    flags |= C_NO_WIN
            except Exception:
                pass
            kwargs["creationflags"] = flags

            si = kwargs.get("startupinfo")
            if si is None:
                kwargs["startupinfo"] = si_hidden
            else:
                try:
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = subprocess.SW_HIDE
                except Exception:
                    kwargs["startupinfo"] = si_hidden
            return original_popen(*args, **kwargs)

        subprocess.Popen = patched_popen
        yield
    finally:
        subprocess.Popen = original_popen


def get_optimal_workers():
    logical_cores = os.cpu_count() or 1
    suggested_workers = max(2, int(logical_cores * 1.5))
    max_workers_cap = 8
    return min(suggested_workers, max_workers_cap)


def calculate_total_size(directory):
    """Calculate the total size of all files in a directory."""
    total_size = 0
    ignore_names = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}
    for dirpath, _, filenames in os.walk(directory):
        for f in filenames:
            if f in ignore_names:
                continue
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp) and not os.path.islink(fp):
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
    return total_size


tooltip_stylesheet = """\
QToolTip {
    background-color: #2b2b2b;
    color: #ffffff;
    border: 1px solid #555;
    padding: 4px;
    border-radius: 5px;
    opacity: 200;
    font-size: 9pt;
}
"""

label_stylesheet = """\
QLabel {
    color: white;
    font-family: 'Segoe UI';
    font-size: 10pt;
}
"""


def show_warning(parent, title, content, orient=Qt.Horizontal, position=InfoBarPosition.TOP_RIGHT,
                 closable=True, duration=2000):
    InfoBar.warning(title=title, content=content, orient=orient, isClosable=closable,
                    position=position, duration=duration, parent=parent)


def show_success(parent, title, content, orient=Qt.Horizontal, position=InfoBarPosition.TOP_RIGHT,
                 closable=True, duration=2000):
    InfoBar.success(title=title, content=content, orient=orient, isClosable=closable,
                    position=position, duration=duration, parent=parent)


def show_error(parent, title, content, orient=Qt.Horizontal, position=InfoBarPosition.TOP_RIGHT,
               closable=True, duration=5000):
    InfoBar.error(title=title, content=content, orient=orient, isClosable=closable,
                  position=position, duration=duration, parent=parent)


def show_info(parent, title, content, orient=Qt.Horizontal, position=InfoBarPosition.TOP_RIGHT,
              closable=True, duration=2000):
    InfoBar.info(title=title, content=content, orient=orient, isClosable=closable,
                 position=position, duration=duration, parent=parent)
