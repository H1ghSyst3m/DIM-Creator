import os
import sys
import re
import subprocess
import shutil
import stat
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from PySide6.QtCore import QStandardPaths, Qt
from qfluentwidgets import InfoBar, InfoBarPosition
from logger_utils import get_logger

log = get_logger(__name__)


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

BUILDS_DIR = os.path.join(DOC_MAIN_DIR, "Builds")
SESSIONS_DIR = os.path.join(DOC_MAIN_DIR, "Sessions")
SESSION_FILE = os.path.join(SESSIONS_DIR, "session.json")
SESSION_BACKUPS_DIR = os.path.join(SESSIONS_DIR, "backups")

IGNORE_SYSTEM_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}


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
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_SYSTEM_FILES]
        
        for f in filenames:
            if f in IGNORE_SYSTEM_FILES:
                continue
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp) and not os.path.islink(fp):
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
    return total_size


def find_7z_executable():
    for name in ('7z', '7za'):
        path = shutil.which(name)
        if path:
            return path
    return None


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


def format_file_size(size_bytes):
    if size_bytes < 0:
        return "Invalid file size: negative value"
    
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def ensure_builds_directory_structure():
    os.makedirs(BUILDS_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(SESSION_BACKUPS_DIR, exist_ok=True)


def _validate_folder_name(folder_name: str) -> None:
    if not folder_name:
        raise ValueError("folder_name cannot be empty")
    
    if '/' in folder_name or '\\' in folder_name or '..' in folder_name:
        raise ValueError(f"folder_name contains invalid path separators or traversal sequences: {folder_name}")
    
    if not re.match(r'^Build\d+$', folder_name):
        raise ValueError(f"folder_name must match pattern 'Build' followed by one or more digits (e.g., 'Build1', 'Build01', 'Build123'): {folder_name}")


def get_build_content_dir(folder_name: str) -> str:
    _validate_folder_name(folder_name)
    return os.path.join(BUILDS_DIR, folder_name, "Content")


def get_build_dir(folder_name: str) -> str:
    _validate_folder_name(folder_name)
    return os.path.join(BUILDS_DIR, folder_name)


def create_build_folder(folder_name: str) -> str:
    _validate_folder_name(folder_name)
    content_dir = get_build_content_dir(folder_name)
    os.makedirs(content_dir, exist_ok=True)
    return content_dir


def delete_build_folder(folder_name: str) -> None:
    _validate_folder_name(folder_name)
    build_path = os.path.join(BUILDS_DIR, folder_name)
    if os.path.exists(build_path):
        shutil.rmtree(build_path)


def _handle_readonly_error(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        log.error(f"Failed to handle readonly error for {path}: {e}")


def clean_build_content(folder_name: str) -> None:
    _validate_folder_name(folder_name)
    build_path = os.path.join(BUILDS_DIR, folder_name)
    
    if not os.path.exists(build_path):
        return
    
    for item in os.listdir(build_path):
        item_path = os.path.join(build_path, item)
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path, onerror=_handle_readonly_error)
        except Exception as e:
            try:
                if not os.path.isdir(item_path):
                    os.chmod(item_path, stat.S_IWRITE)
                    os.unlink(item_path)
                else:
                    log = get_logger(__name__)
                    log.error(f"Failed to delete directory {item_path}: {e}")
            except Exception as e2:
                log = get_logger(__name__)
                log.error(f"Failed to delete {item_path}: {e2}")
    
    content_dir = os.path.join(build_path, "Content")
    os.makedirs(content_dir, exist_ok=True)


def create_session_backup() -> None:
    if not os.path.exists(SESSION_FILE):
        return
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_name = f"session_{timestamp}.json"
    backup_path = os.path.join(SESSION_BACKUPS_DIR, backup_name)
    
    shutil.copy2(SESSION_FILE, backup_path)
    
    try:
        backups = sorted([
            f for f in os.listdir(SESSION_BACKUPS_DIR)
            if f.startswith("session_") and f.endswith(".json")
        ])
    except OSError:
        backups = []
    
    while len(backups) > 5:
        oldest = backups.pop(0)
        try:
            os.remove(os.path.join(SESSION_BACKUPS_DIR, oldest))
        except OSError as e:
            log.error(f"Failed to delete old backup {oldest}: {e}")
            


def delete_session_file() -> None:
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
            log.info("Session file deleted")
        except OSError as e:
            log.error(f"Failed to delete session file: {e}")
            raise


def delete_all_build_folders(handle_error_callback=None) -> list[str]:
    if not os.path.exists(BUILDS_DIR):
        return []
    
    failed = []
    
    try:
        for item in os.listdir(BUILDS_DIR):
            item_path = os.path.join(BUILDS_DIR, item)
            
            if os.path.isdir(item_path) and not os.path.islink(item_path) and re.match(r'^Build\d+$', item):
                try:
                    if handle_error_callback:
                        shutil.rmtree(item_path, onerror=handle_error_callback)
                    else:
                        shutil.rmtree(item_path)
                    log.info(f"Deleted build folder: {item}")
                except (OSError, shutil.Error) as e:
                    log.error(f"Failed to delete build folder {item}: {e}")
                    failed.append(item)
    except OSError as e:
        log.error(f"Failed to access Builds directory: {e}")
        raise
    
    return failed

