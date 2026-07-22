import os
import sys
import re
import subprocess
import shutil
import stat
from pathlib import Path
from contextlib import contextmanager
from PySide6.QtCore import QFile, QStandardPaths, Qt
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
BUILDS_DIR = os.path.join(DOC_MAIN_DIR, "Builds")
SESSIONS_DIR = os.path.join(DOC_MAIN_DIR, "Sessions")
SESSION_FILE = os.path.join(SESSIONS_DIR, "session.json")
SESSION_BACKUPS_DIR = os.path.join(SESSIONS_DIR, "backups")
ASSETS_DIR = os.path.join(DOC_MAIN_DIR, "Assets")
COVERS_DIR = os.path.join(ASSETS_DIR, "Covers")

IGNORE_SYSTEM_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}


@contextmanager
def suppress_cmd_window():
    """Backward-compatible no-op; callers must configure their own process."""
    yield


def hidden_subprocess_kwargs() -> dict:
    """Return per-process flags that keep helper consoles hidden on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


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


def _trusted_executable(path: str, *, boundary: str | None = None) -> str | None:
    """Return a fixed executable path only when it cannot redirect elsewhere."""
    if not path or not os.path.isabs(path):
        return None
    candidate = os.path.abspath(path)
    try:
        candidate_stat = os.lstat(candidate)
    except OSError:
        return None
    if (
        not stat.S_ISREG(candidate_stat.st_mode)
        or os.path.islink(candidate)
        or bool(
            getattr(candidate_stat, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    ):
        return None
    if boundary is not None and has_reparse_component(candidate, boundary):
        return None
    resolved = os.path.realpath(candidate)
    if os.name != "nt" and not os.access(candidate, os.X_OK):
        return None
    return resolved


def _safe_path_directories() -> list[str]:
    current_directory = canonical_path(os.getcwd())
    directories = []
    seen = set()
    for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = raw_entry.strip().strip('"')
        if not entry or not os.path.isabs(entry) or not os.path.isdir(entry):
            continue
        resolved = canonical_path(entry)
        if resolved in seen or is_path_within(resolved, current_directory):
            continue
        if has_reparse_point(entry):
            continue
        seen.add(resolved)
        directories.append(entry)
    return directories


def _find_on_safe_path(names: tuple[str, ...]) -> str | None:
    for directory in _safe_path_directories():
        for name in names:
            executable = _trusted_executable(
                os.path.join(directory, name), boundary=directory
            )
            if executable:
                return executable
    return None


def _program_files_roots() -> list[str]:
    if os.name != "nt":
        return []
    candidates = [
        os.environ.get("ProgramW6432"),
        os.environ.get("ProgramFiles"),
        r"C:\Program Files",
        os.environ.get("ProgramFiles(x86)"),
        r"C:\Program Files (x86)",
    ]
    roots = []
    seen = set()
    for candidate in candidates:
        if not candidate or not os.path.isabs(candidate):
            continue
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized not in seen:
            seen.add(normalized)
            roots.append(candidate)
    return roots


def find_7z_executable():
    if os.name == "nt":
        for root in _program_files_roots():
            executable = _trusted_executable(
                os.path.join(root, "7-Zip", "7z.exe"), boundary=root
            )
            if executable:
                return executable
        return _find_on_safe_path(("7z.exe", "7zz.exe", "7za.exe"))
    return _find_on_safe_path(("7z", "7zz", "7za"))


def find_unrar_executable():
    if os.name == "nt":
        for root in _program_files_roots():
            for relative in (
                ("WinRAR", "UnRAR.exe"),
                ("UnRAR", "UnRAR.exe"),
            ):
                executable = _trusted_executable(
                    os.path.join(root, *relative), boundary=root
                )
                if executable:
                    return executable
        return _find_on_safe_path(("UnRAR.exe", "unrar.exe"))
    return _find_on_safe_path(("unrar",))


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
    "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
}


def validate_windows_name(name: str) -> str:
    """Validate one Windows path component and return its stripped value."""
    if not isinstance(name, str):
        raise ValueError("Name must be text")
    value = name.strip()
    if not value or value in {".", ".."}:
        raise ValueError("Name cannot be empty or relative")
    if value != name or value.endswith((".", " ")):
        raise ValueError("Names cannot start/end with spaces or end with a dot")
    if any(ch in value for ch in '<>:"/\\|?*') or any(ord(ch) < 32 for ch in value):
        raise ValueError("Name contains characters that Windows does not allow")
    if len(value) > 255:
        raise ValueError("Name exceeds the Windows 255-character limit")
    stem = value.split(".", 1)[0].rstrip(" .").upper()
    if stem in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"'{value}' is a reserved Windows name")
    return value


def canonical_path(path: str) -> str:
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("Path must be text")
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def is_path_within(path: str, root: str, *, allow_root: bool = True) -> bool:
    try:
        candidate = canonical_path(path)
        boundary = canonical_path(root)
        inside = os.path.commonpath((candidate, boundary)) == boundary
        return inside and (allow_root or candidate != boundary)
    except (OSError, ValueError, TypeError):
        return False


def has_reparse_component(
    path: str, root: str, *, include_root: bool = True
) -> bool:
    """Return whether an in-root path traverses a link or reparse point."""
    if not is_path_within(path, root):
        return True
    candidate = os.path.abspath(os.fspath(path))
    boundary = os.path.abspath(os.fspath(root))
    try:
        if os.path.normcase(os.path.commonpath((candidate, boundary))) != os.path.normcase(boundary):
            return True
        if include_root and has_reparse_point(boundary):
            return True
        relative = os.path.relpath(candidate, boundary)
        if relative == ".":
            return False
        current = boundary
        for component in Path(relative).parts:
            current = os.path.join(current, component)
            if os.path.lexists(current) and has_reparse_point(current):
                return True
        return False
    except (OSError, TypeError, ValueError):
        return True


def safe_child_path(root: str, parent: str, name: str) -> str:
    value = validate_windows_name(name)
    if not is_path_within(parent, root):
        raise ValueError("Destination is outside the Content folder")
    if has_reparse_component(parent, root):
        raise ValueError("Links and reparse points cannot be used as destinations")
    result = os.path.join(parent, value)
    if not is_path_within(result, root, allow_root=False):
        raise ValueError("Destination is outside the Content folder")
    if has_reparse_component(result, root):
        raise ValueError("Links and reparse points cannot be used as destinations")
    return result


def has_reparse_point(path: str) -> bool:
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
        flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return os.path.islink(path) or bool(attrs & flag)
    except OSError:
        return True


def move_to_trash(path: str) -> bool:
    try:
        result = QFile.moveToTrash(os.path.abspath(path))
    except (OSError, RuntimeError):
        return False
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


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
    os.makedirs(DOC_MAIN_DIR, exist_ok=True)
    os.makedirs(BUILDS_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(SESSION_BACKUPS_DIR, exist_ok=True)
    os.makedirs(COVERS_DIR, exist_ok=True)


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


def _checked_build_path(folder_name: str) -> str:
    _validate_folder_name(folder_name)
    builds_root = os.path.abspath(BUILDS_DIR)
    if os.path.lexists(builds_root):
        if not os.path.isdir(builds_root) or has_reparse_point(builds_root):
            raise OSError("The managed Builds directory is unsafe")
    build_path = os.path.join(builds_root, folder_name)
    if os.path.lexists(build_path) and has_reparse_component(
        build_path, builds_root
    ):
        raise OSError(f"Build folder contains a link or reparse point: {folder_name}")
    return build_path


def _assert_regular_build_tree(build_path: str) -> None:
    if not os.path.isdir(build_path) or has_reparse_point(build_path):
        raise OSError(f"Build folder is not a regular directory: {build_path}")
    for current, directories, files in os.walk(
        build_path, topdown=True, followlinks=False
    ):
        for name in (*directories, *files):
            candidate = os.path.join(current, name)
            if has_reparse_point(candidate):
                raise OSError(
                    "Build cleanup refuses links and reparse points: "
                    f"{candidate}"
                )


def create_build_folder(folder_name: str) -> str:
    os.makedirs(BUILDS_DIR, exist_ok=True)
    build_path = _checked_build_path(folder_name)
    content_dir = os.path.join(build_path, "Content")
    os.makedirs(content_dir, exist_ok=True)
    if has_reparse_component(content_dir, BUILDS_DIR):
        raise OSError("Build Content directory contains a link or reparse point")
    return content_dir


def delete_build_folder(folder_name: str) -> None:
    build_path = _checked_build_path(folder_name)
    if os.path.isdir(build_path):
        _assert_regular_build_tree(build_path)
        shutil.rmtree(build_path)


def _handle_readonly_error(func, path, exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def clean_build_content(folder_name: str) -> None:
    build_path = _checked_build_path(folder_name)
    
    if not os.path.isdir(build_path):
        return
    _assert_regular_build_tree(build_path)
    
    failures = []
    for item in os.listdir(build_path):
        item_path = os.path.join(build_path, item)
        try:
            if os.path.isfile(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path, onerror=_handle_readonly_error)
        except Exception as e:
            failures.append(f"{item}: {e}")

    try:
        remaining = os.listdir(build_path)
    except OSError as exc:
        remaining = []
        failures.append(str(exc))
    if remaining:
        failures.extend(f"still present: {item}" for item in remaining)
    if failures:
        raise OSError("Build cleanup was incomplete: " + "; ".join(failures))
    
    content_dir = os.path.join(build_path, "Content")
    os.makedirs(content_dir, exist_ok=True)



def delete_all_build_folders(handle_error_callback=None) -> list[str]:
    if not os.path.exists(BUILDS_DIR):
        return []
    if not os.path.isdir(BUILDS_DIR) or has_reparse_point(BUILDS_DIR):
        raise OSError("The managed Builds directory is unsafe")
    
    failed = []
    
    try:
        for item in os.listdir(BUILDS_DIR):
            item_path = os.path.join(BUILDS_DIR, item)

            if os.path.lexists(item_path) and re.match(r'^Build\d+$', item):
                try:
                    item_path = _checked_build_path(item)
                    if not os.path.isdir(item_path):
                        raise OSError("Build path is not a directory")
                    _assert_regular_build_tree(item_path)
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

