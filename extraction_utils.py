from __future__ import annotations

import errno
import io
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import QThread, Signal

from logger_utils import get_logger
from utils import (
    WINDOWS_RESERVED_NAMES,
    downloads_dir,
    find_7z_executable,
    find_unrar_executable,
    hidden_subprocess_kwargs,
)


log = get_logger(__name__)

ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z")
MAX_ARCHIVE_ENTRIES = 200_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 100 * 1024**3
MAX_FILE_UNCOMPRESSED_BYTES = 50 * 1024**3
MAX_EMBEDDED_ARCHIVES = 100
MAX_NESTING_DEPTH = 1
MIN_FREE_SPACE_BYTES = 2 * 1024**3
SUSPICIOUS_RATIO = 1000
SUSPICIOUS_RATIO_MIN_BYTES = 1024**3
EXTERNAL_TOTAL_TIMEOUT_SECONDS = 4 * 60 * 60
EXTERNAL_PROGRESS_TIMEOUT_SECONDS = 5 * 60
MAX_EXTERNAL_OUTPUT_CHARS = 256 * 1024**2
MAX_EXTERNAL_LINE_CHARS = 64 * 1024
MAX_EXTERNAL_RECORD_FIELDS = 64
COPY_CHUNK_SIZE = 1024 * 1024

_IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "__macosx"}
_INVALID_WINDOWS_CHARS = set('<>"|?*')
_TEMPLATE_WORD = re.compile(r"(?<![A-Za-z0-9])templates?(?![A-Za-z0-9])", re.IGNORECASE)


class ConflictPolicy(str, Enum):
    REPLACE = "replace"
    SKIP = "skip"
    CANCEL = "cancel"

    @classmethod
    def coerce(cls, value: ConflictPolicy | str) -> ConflictPolicy:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).casefold())
        except ValueError as exc:
            raise ValueError(f"Unknown conflict policy: {value}") from exc


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    size: int
    compressed_size: int
    is_dir: bool = False


@dataclass(frozen=True)
class ArchiveInventory:
    archive_path: str
    members: tuple[ArchiveMember, ...]
    total_uncompressed: int
    embedded_archives: int


@dataclass(frozen=True)
class ExtractionBuildPlan:
    part: int
    archive_path: str
    content_dir: str = ""
    build_id: str | None = None


@dataclass(frozen=True)
class ExtractionPlan:
    builds: tuple[ExtractionBuildPlan, ...]
    template_archives: tuple[str, ...] = ()
    conflict_policy: ConflictPolicy = ConflictPolicy.CANCEL


@dataclass(frozen=True)
class PlannedEmbeddedArchive:
    relative_path: str
    staged_path: str


@dataclass
class ArchiveImportPlan:
    stage_root: str
    direct_content_root: str | None
    content_archives: tuple[PlannedEmbeddedArchive, ...] = ()
    template_archives: tuple[PlannedEmbeddedArchive, ...] = ()
    ignored_archives: tuple[PlannedEmbeddedArchive, ...] = ()
    warning: str | None = None
    budget_entries: int = 0
    budget_uncompressed: int = 0
    budget_embedded_archives: int = 0
    _claimed: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def is_direct_content(self) -> bool:
        return self.direct_content_root is not None

    def claim(self) -> None:
        with self._lock:
            if self._closed:
                raise ExtractionError("Archive import plan has already been cleaned up.")
            if self._claimed:
                raise ExtractionError("Archive import plan has already been consumed.")
            self._claimed = True
        self.validate()

    def validate(self) -> None:
        stage_root = os.path.abspath(self.stage_root)
        if not os.path.isdir(stage_root) or _is_link_or_reparse(stage_root):
            raise ExtractionError("Archive import staging area is no longer available.")
        paths = [item.staged_path for item in self.content_archives]
        paths.extend(item.staged_path for item in self.template_archives)
        paths.extend(item.staged_path for item in self.ignored_archives)
        if self.direct_content_root:
            paths.append(self.direct_content_root)
        for path in paths:
            candidate = os.path.abspath(path)
            try:
                contained = os.path.commonpath((stage_root, candidate)) == stage_root
            except ValueError:
                contained = False
            if not contained or not os.path.exists(candidate) or _is_link_or_reparse(candidate):
                raise UnsafeArchiveError("Archive import plan contains an unsafe staged path.")

    def initial_budget(self):
        return _ExtractionBudget(
            self.budget_entries,
            self.budget_uncompressed,
            self.budget_embedded_archives,
        )

    def cleanup(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        shutil.rmtree(self.stage_root, ignore_errors=True)


@dataclass(frozen=True)
class ArchivePlanningResult:
    status: str
    message: str = ""
    plan: ArchiveImportPlan | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


@dataclass
class ExtractionResult:
    status: str
    message: str = ""
    modified_builds: list[str] = field(default_factory=list)
    copied_templates: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    new_builds: list[dict[str, Any]] = field(default_factory=list)
    next_build_number: int | None = None
    _transaction: _FileTransaction | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    @property
    def rollback_pending(self) -> bool:
        return bool(
            self._transaction is not None
            and self._transaction.rollback_pending
        )

    def finalize(self) -> None:
        """Discard rollback backups after the UI persisted the session update."""
        transaction = self._transaction
        self._transaction = None
        if transaction is not None:
            transaction.finalize()

    def rollback(self) -> None:
        """Restore committed files if applying or persisting the result fails."""
        transaction = self._transaction
        if transaction is not None:
            transaction.rollback()
            self._transaction = None


class ExtractionError(RuntimeError):
    pass


class ExtractionRollbackError(ExtractionError):
    def __init__(
        self,
        failures: Sequence[tuple[str, OSError]],
        *,
        original_error: BaseException | None = None,
    ):
        self.failures = tuple(failures)
        self.original_error = original_error
        preview = "; ".join(
            f"{path}: {error}" for path, error in self.failures[:3]
        )
        if len(self.failures) > 3:
            preview += f"; and {len(self.failures) - 3} more"
        super().__init__(
            f"Rollback incomplete for {len(self.failures)} path(s): {preview}"
        )


class UnsafeArchiveError(ExtractionError):
    pass


class ArchiveToolUnavailable(ExtractionError):
    pass


class ExtractionCancelled(ExtractionError):
    pass


class ExtractionConflict(ExtractionCancelled):
    pass


class MultipartArchiveError(ValueError):
    pass


def _check_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise ExtractionCancelled("Extraction cancelled.")


def is_template_archive(path: str) -> bool:
    stem = os.path.splitext(os.path.basename(path))[0]
    return bool(_TEMPLATE_WORD.search(stem))


def classify_archives(archive_files, enable_template_detection):
    content_archives = []
    template_archives = []
    ignored_archives = []

    for archive_path in archive_files:
        if is_template_archive(archive_path):
            if enable_template_detection:
                template_archives.append(archive_path)
            else:
                ignored_archives.append(archive_path)
        else:
            content_archives.append(archive_path)

    return content_archives, template_archives, ignored_archives


def _ordered_parts(
    matches: Sequence[tuple[int, int | None, str]],
    pattern_name: str,
) -> list[str]:
    part_numbers = [part for part, _, _ in matches]
    if len(part_numbers) != len(set(part_numbers)):
        raise MultipartArchiveError(
            f"Duplicate part numbers detected in {pattern_name}: {part_numbers}"
        )

    declared_totals = {total for _, total, _ in matches if total is not None}
    if len(declared_totals) > 1:
        raise MultipartArchiveError(
            f"Conflicting multipart totals detected in {pattern_name}."
        )
    expected_total = next(iter(declared_totals), len(matches))
    if expected_total < 1 or expected_total > 99:
        raise MultipartArchiveError(
            f"Multipart total must be between 1 and 99, got {expected_total}."
        )

    expected = set(range(1, expected_total + 1))
    actual = set(part_numbers)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing parts {missing}")
        if extra:
            details.append(f"unexpected parts {extra}")
        raise MultipartArchiveError(
            f"Incomplete multipart archive ({', '.join(details)})."
        )

    return [path for _, _, path in sorted(matches, key=lambda item: item[0])]


def detect_heuristic_ordering(archive_files):
    """Order multipart archives and reject duplicate or incomplete sequences."""
    archive_files = list(archive_files)
    if not archive_files:
        return [], None
    if len(archive_files) > 99:
        raise MultipartArchiveError("A DIM package can contain at most 99 parts.")

    patterns = (
        (
            "XofY pattern",
            re.compile(r"_(\d+)of(\d+)(?=\D|$)", re.IGNORECASE),
            True,
        ),
        (
            "Part pattern",
            re.compile(
                r"(?:^|[^A-Za-z0-9])part\s*(\d+)"
                r"(?:\s*(?:of|-)\s*(\d+))?(?=\D|$)",
                re.IGNORECASE,
            ),
            True,
        ),
        (
            "build-number pattern",
            re.compile(r"(?<!\d)(\d{1,2})_\d{5,}\.(?:zip|rar|7z)$", re.IGNORECASE),
            False,
        ),
        (
            "trailing-number pattern",
            re.compile(r"(?<!\d)_(\d{1,2})\.(?:zip|rar|7z)$", re.IGNORECASE),
            False,
        ),
    )

    for pattern_name, pattern, supports_total in patterns:
        matches = []
        for archive_path in archive_files:
            match = pattern.search(os.path.basename(archive_path))
            if not match:
                continue
            total = int(match.group(2)) if supports_total and match.lastindex and match.lastindex >= 2 and match.group(2) else None
            matches.append((int(match.group(1)), total, archive_path))
        if matches:
            if len(matches) != len(archive_files):
                raise MultipartArchiveError(
                    f"Mixed or incomplete {pattern_name}: every selected archive "
                    "must use the same numbering scheme."
                )
            return _ordered_parts(matches, pattern_name), None

    if len(archive_files) == 1:
        return archive_files, None

    return (
        sorted(archive_files, key=lambda path: os.path.basename(path).casefold()),
        "Could not detect build numbering pattern. Archives ordered alphabetically.",
    )


def _normalise_member_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise UnsafeArchiveError("Archive contains an empty or invalid path.")

    path = raw_path.replace("\\", "/")
    if path.startswith("/") or path.startswith("//") or re.match(r"^[A-Za-z]:", path):
        raise UnsafeArchiveError(f"Archive contains an absolute path: {raw_path}")

    parts = []
    for part in PurePosixPath(path).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafeArchiveError(f"Archive path escapes its destination: {raw_path}")
        if part.endswith((" ", ".")):
            raise UnsafeArchiveError(f"Archive path has a trailing dot or space: {raw_path}")
        if ":" in part:
            raise UnsafeArchiveError(f"Archive path contains an NTFS stream or drive: {raw_path}")
        if any(char in _INVALID_WINDOWS_CHARS or ord(char) < 32 for char in part):
            raise UnsafeArchiveError(f"Archive path contains invalid Windows characters: {raw_path}")
        device_name = part.split(".", 1)[0].rstrip(" .").upper()
        if device_name in WINDOWS_RESERVED_NAMES:
            raise UnsafeArchiveError(f"Archive path uses a Windows device name: {raw_path}")
        parts.append(part)

    if not parts:
        raise UnsafeArchiveError(f"Archive contains an invalid root entry: {raw_path}")
    return "/".join(parts)


def _is_archive_name(path: str) -> bool:
    return path.casefold().endswith(ARCHIVE_SUFFIXES)


def _validate_inventory(
    archive_path: str,
    members: Iterable[ArchiveMember],
    cancel_check: Callable[[], bool] | None = None,
) -> ArchiveInventory:
    validated = []
    explicit_members: dict[str, str] = {}
    path_nodes: dict[str, tuple[str, bool]] = {}
    total_size = 0
    total_file_size = 0
    total_compressed = 0
    embedded_count = 0

    for member in members:
        _check_cancelled(cancel_check)
        path = _normalise_member_path(member.path)
        key = path.casefold()
        previous = explicit_members.get(key)
        if previous is not None:
            raise UnsafeArchiveError(
                f"Archive contains a case-insensitive path collision: {previous} / {path}"
            )
        explicit_members[key] = path

        parts = path.split("/")
        for index in range(1, len(parts) + 1):
            node_path = "/".join(parts[:index])
            node_key = node_path.casefold()
            node_is_dir = index < len(parts) or member.is_dir
            existing = path_nodes.get(node_key)
            if existing is None:
                path_nodes[node_key] = (node_path, node_is_dir)
                continue
            existing_path, existing_is_dir = existing
            if existing_path != node_path:
                raise UnsafeArchiveError(
                    "Archive contains a case-insensitive path collision: "
                    f"{existing_path} / {node_path}"
                )
            if existing_is_dir != node_is_dir:
                raise UnsafeArchiveError(
                    "Archive uses a file as a parent directory or reuses a path "
                    f"as both file and directory: {node_path}"
                )

        if member.size < 0 or member.compressed_size < 0:
            raise UnsafeArchiveError(f"Archive contains an invalid size for {path}.")
        if member.size > MAX_FILE_UNCOMPRESSED_BYTES:
            raise UnsafeArchiveError(
                f"Archive member exceeds the 50 GiB limit: {path}"
            )
        if (
            not member.is_dir
            and member.size >= SUSPICIOUS_RATIO_MIN_BYTES
            and member.size / max(member.compressed_size, 1) > SUSPICIOUS_RATIO
        ):
            raise UnsafeArchiveError(
                f"Archive member has a suspicious compression ratio: {path}"
            )

        total_size += member.size
        if not member.is_dir:
            total_file_size += member.size
            total_compressed += member.compressed_size
        if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise UnsafeArchiveError("Archive exceeds the 100 GiB uncompressed size limit.")
        if not member.is_dir and _is_archive_name(path):
            embedded_count += 1
        validated.append(
            ArchiveMember(path, member.size, member.compressed_size, member.is_dir)
        )

    if len(validated) > MAX_ARCHIVE_ENTRIES:
        raise UnsafeArchiveError("Archive exceeds the 200,000 entry limit.")
    if embedded_count > MAX_EMBEDDED_ARCHIVES:
        raise UnsafeArchiveError("Archive contains more than 100 embedded archives.")
    if (
        total_file_size >= SUSPICIOUS_RATIO_MIN_BYTES
        and total_file_size / max(total_compressed, 1) > SUSPICIOUS_RATIO
    ):
        raise UnsafeArchiveError(
            "Archive has a suspicious aggregate compression ratio."
        )

    return ArchiveInventory(
        archive_path=os.path.abspath(archive_path),
        members=tuple(validated),
        total_uncompressed=total_size,
        embedded_archives=embedded_count,
    )


def _stat_is_link_or_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _is_link_or_reparse(path: str) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return _stat_is_link_or_reparse(info)


def _safe_destination(root: str, relative_path: str) -> str:
    relative_path = _normalise_member_path(relative_path)
    root_abs = os.path.abspath(root)
    destination = os.path.abspath(
        os.path.join(root_abs, *relative_path.split("/"))
    )
    try:
        contained = os.path.commonpath((root_abs, destination)) == root_abs
    except ValueError:
        contained = False
    if not contained:
        raise UnsafeArchiveError(f"Path escapes extraction root: {relative_path}")
    return destination


def _ensure_free_space(path: str, required_bytes: int) -> None:
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    free = shutil.disk_usage(probe).free
    if free - required_bytes < MIN_FREE_SPACE_BYTES:
        raise ExtractionError(
            "Not enough free disk space. At least 2 GiB must remain after extraction."
        )


def _volume_key(path: str) -> tuple[str, int | str]:
    """Return a stable key for aggregating planned writes on one volume."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        device = os.stat(probe).st_dev
    except OSError:
        device = 0
    drive = os.path.splitdrive(probe)[0].casefold()
    return ("device", device) if device else ("drive", drive or probe.casefold())


def _copy_file(
    source: str,
    destination: str,
    cancel_check: Callable[[], bool],
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    def identity(info: os.stat_result) -> tuple[int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        )

    source = os.path.abspath(source)
    destination = os.path.abspath(destination)
    source_before = os.lstat(source)
    if expected_stat is not None and identity(source_before) != identity(expected_stat):
        raise UnsafeArchiveError(
            "Source file changed before it could be copied."
        )
    if (
        not stat.S_ISREG(source_before.st_mode)
        or _stat_is_link_or_reparse(source_before)
    ):
        raise UnsafeArchiveError("Copy source must be a regular file.")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    source_fd = -1
    destination_created = False
    final_source_stat = source_before
    try:
        try:
            source_fd = os.open(source, flags)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise UnsafeArchiveError(
                    "Copy source became a link while being opened."
                ) from exc
            raise

        opened_stat = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or _stat_is_link_or_reparse(opened_stat)
        ):
            raise UnsafeArchiveError("Copy source must be a regular file.")
        if identity(opened_stat) != identity(source_before):
            raise UnsafeArchiveError(
                "Source file changed while being opened."
            )

        os.makedirs(os.path.dirname(destination), exist_ok=True)
        copied_bytes = 0
        with os.fdopen(source_fd, "rb") as src:
            source_fd = -1
            with open(destination, "xb") as dst:
                destination_created = True
                while True:
                    if cancel_check():
                        raise ExtractionCancelled("Extraction cancelled.")
                    chunk = src.read(COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    dst.write(chunk)
                    copied_bytes += len(chunk)
            final_source_stat = os.fstat(src.fileno())

        if (
            identity(final_source_stat) != identity(source_before)
            or copied_bytes != source_before.st_size
        ):
            raise UnsafeArchiveError(
                "Source file changed while it was being copied."
            )
    except BaseException:
        if destination_created and os.path.lexists(destination):
            try:
                os.remove(destination)
            except OSError as cleanup_error:
                log.warning(
                    "Failed to remove incomplete copied file %s: %s",
                    destination,
                    cleanup_error,
                )
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)

    try:
        os.chmod(destination, stat.S_IMODE(final_source_stat.st_mode))
        os.utime(
            destination,
            ns=(final_source_stat.st_atime_ns, final_source_stat.st_mtime_ns),
        )
    except OSError:
        pass


def _snapshot_archive(
    source_path: str,
    snapshot_root: str,
    cancel_check: Callable[[], bool],
) -> str:
    """Copy an untrusted archive so inventory and extraction share exact bytes."""
    source = os.path.abspath(source_path)
    try:
        source_stat = os.lstat(source)
    except OSError as exc:
        raise ExtractionError(f"Cannot read archive: {source}") from exc
    if not stat.S_ISREG(source_stat.st_mode) or _is_link_or_reparse(source):
        raise UnsafeArchiveError("Archive source must be a regular file.")
    suffix = os.path.splitext(source)[1].casefold()
    if suffix not in ARCHIVE_SUFFIXES:
        raise ExtractionError(f"Unsupported archive type: {suffix or '<none>'}")
    _ensure_free_space(snapshot_root, source_stat.st_size)
    os.makedirs(snapshot_root, exist_ok=True)
    snapshot = os.path.join(snapshot_root, f"source{suffix}")
    _copy_file(
        source,
        snapshot,
        cancel_check,
        expected_stat=source_stat,
    )
    return snapshot


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=3)
        except Exception:
            pass


def _run_external(
    arguments: Sequence[str],
    cancel_check: Callable[[], bool],
    *,
    output_parser: Callable[[Iterable[str], Callable[[], bool]], Any] | None = None,
    capture_output: bool = True,
) -> Any:
    """Run an archive tool while spooling merged output outside process memory."""
    with tempfile.TemporaryFile(mode="w+b", buffering=0) as spool:
        process = subprocess.Popen(
            list(arguments),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=spool,
            stderr=subprocess.STDOUT,
            **hidden_subprocess_kwargs(),
        )
        started = time.monotonic()
        last_progress = started
        observed_length = 0

        try:
            while process.poll() is None:
                if cancel_check():
                    raise ExtractionCancelled("Extraction cancelled.")
                now = time.monotonic()
                if now - started > EXTERNAL_TOTAL_TIMEOUT_SECONDS:
                    raise ExtractionError(
                        "Archive tool exceeded the four-hour time limit."
                    )
                output_length = os.fstat(spool.fileno()).st_size
                if output_length > MAX_EXTERNAL_OUTPUT_CHARS:
                    raise UnsafeArchiveError(
                        "Archive tool produced excessive output."
                    )
                if output_length > observed_length:
                    observed_length = output_length
                    last_progress = now
                elif now - last_progress > EXTERNAL_PROGRESS_TIMEOUT_SECONDS:
                    raise ExtractionError(
                        "Archive tool made no progress for five minutes."
                    )
                time.sleep(0.1)

            output_length = os.fstat(spool.fileno()).st_size
            if output_length > MAX_EXTERNAL_OUTPUT_CHARS:
                raise UnsafeArchiveError("Archive tool produced excessive output.")
            if process.returncode != 0:
                spool.seek(max(0, output_length - 4096))
                detail = spool.read().decode("utf-8", errors="replace")
                lowered = detail.casefold()
                if "password" in lowered or "encrypted" in lowered:
                    raise UnsafeArchiveError(
                        "Password-protected archives are not supported."
                    )
                raise ExtractionError(
                    "Archive tool failed with exit code "
                    f"{process.returncode}: {detail.strip()[-1200:]}"
                )

            if output_parser is not None:
                spool.seek(0)
                text_output = io.TextIOWrapper(
                    spool, encoding="utf-8", errors="replace", newline=None
                )
                try:
                    return output_parser(text_output, cancel_check)
                finally:
                    text_output.detach()
            if not capture_output:
                return ""
            spool.seek(0)
            return spool.read().decode("utf-8", errors="replace")
        except BaseException:
            _stop_process(process)
            raise


class _ZipAdapter:
    def __init__(
        self,
        archive_path: str,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.archive_path = archive_path
        self.cancel_check = cancel_check
        try:
            self._zip = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise ExtractionError(f"Invalid ZIP archive: {os.path.basename(archive_path)}") from exc
        self._infos: list[zipfile.ZipInfo] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._zip.close()

    def inventory(self) -> ArchiveInventory:
        members = []
        infos = self._zip.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise UnsafeArchiveError("Archive exceeds the 200,000 entry limit.")
        for info in infos:
            _check_cancelled(self.cancel_check)
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError("Password-protected archives are not supported.")
            unix_mode = info.external_attr >> 16
            file_type = stat.S_IFMT(unix_mode)
            if file_type == stat.S_IFLNK:
                raise UnsafeArchiveError(f"Archive contains a symbolic link: {info.filename}")
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise UnsafeArchiveError(f"Archive contains a special file: {info.filename}")
            dos_attributes = info.external_attr & 0xFFFF
            if dos_attributes & 0x400:
                raise UnsafeArchiveError(f"Archive contains a reparse point: {info.filename}")
            members.append(
                ArchiveMember(
                    info.filename,
                    info.file_size,
                    info.compress_size,
                    info.is_dir(),
                )
            )
        inventory = _validate_inventory(
            self.archive_path, members, self.cancel_check
        )
        self._infos = infos
        return inventory

    def extract(
        self,
        destination: str,
        inventory: ArchiveInventory,
        cancel_check: Callable[[], bool],
    ) -> None:
        if not self._infos:
            raise ExtractionError("ZIP archive was not inventoried before extraction.")
        os.makedirs(destination, exist_ok=True)
        inventory_by_key = {member.path.casefold(): member for member in inventory.members}

        for info in self._infos:
            if cancel_check():
                raise ExtractionCancelled("Extraction cancelled.")
            normalised = _normalise_member_path(info.filename)
            member = inventory_by_key[normalised.casefold()]
            target = _safe_destination(destination, normalised)
            if member.is_dir:
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.lexists(target):
                raise UnsafeArchiveError(f"Archive member would overwrite another member: {normalised}")
            copied = 0
            try:
                with self._zip.open(info, "r") as source, open(target, "xb") as output:
                    while True:
                        if cancel_check():
                            raise ExtractionCancelled("Extraction cancelled.")
                        chunk = source.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
                        copied += len(chunk)
            except ExtractionCancelled:
                raise
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                raise ExtractionError(f"Failed to extract ZIP member {normalised}: {exc}") from exc
            if copied != member.size:
                raise ExtractionError(f"ZIP member size changed during extraction: {normalised}")

        _validate_extracted_tree(destination, inventory, cancel_check)


def _iter_external_lines(output: str | Iterable[str]) -> Iterable[str]:
    if isinstance(output, str):
        for line in output.splitlines():
            if len(line) > MAX_EXTERNAL_LINE_CHARS:
                raise UnsafeArchiveError("Archive inventory contains an excessive line.")
            yield line
        return

    readline = getattr(output, "readline", None)
    if callable(readline):
        while True:
            line = readline(MAX_EXTERNAL_LINE_CHARS + 1)
            if not line:
                return
            if len(line) > MAX_EXTERNAL_LINE_CHARS:
                raise UnsafeArchiveError(
                    "Archive inventory contains an excessive line."
                )
            yield line

    reader = iter(output)
    while True:
        try:
            line = next(reader)
        except StopIteration:
            return
        if len(line) > MAX_EXTERNAL_LINE_CHARS:
            raise UnsafeArchiveError("Archive inventory contains an excessive line.")
        yield line


def _append_external_record(
    records: list[dict[str, str]], current: dict[str, str]
) -> None:
    if len(records) >= MAX_ARCHIVE_ENTRIES + 16:
        raise UnsafeArchiveError("Archive exceeds the 200,000 entry limit.")
    records.append(current)


def _set_external_record_value(
    current: dict[str, str], key: str, value: str
) -> None:
    if key not in current and len(current) >= MAX_EXTERNAL_RECORD_FIELDS:
        raise UnsafeArchiveError("Archive inventory record has excessive metadata.")
    current[key] = value


def _parse_slt_records(
    output: str | Iterable[str],
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, str]]:
    records = []
    current = {}
    for line in _iter_external_lines(output):
        _check_cancelled(cancel_check)
        if " = " not in line:
            if not line.strip() and current:
                _append_external_record(records, current)
                current = {}
            continue
        key, value = line.split(" = ", 1)
        if key == "Path" and "Path" in current:
            _append_external_record(records, current)
            current = {}
        _set_external_record_value(current, key.strip(), value.strip())
    if current:
        _append_external_record(records, current)
    return records


def _parse_colon_records(
    output: str | Iterable[str],
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, str]]:
    records = []
    current = {}
    for line in _iter_external_lines(output):
        _check_cancelled(cancel_check)
        stripped = line.strip()
        if not stripped:
            if current:
                _append_external_record(records, current)
                current = {}
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        if key.casefold() == "name" and "Name" in current:
            _append_external_record(records, current)
            current = {}
        _set_external_record_value(
            current, key.strip().title(), value.strip()
        )
    if current:
        _append_external_record(records, current)
    return records


def _integer(record: dict[str, str], *keys: str) -> int:
    for key in keys:
        value = record.get(key)
        if value is not None:
            digits = value.replace(",", "").replace(" ", "")
            if digits.isdigit():
                return int(digits)
    return 0


class _ExternalAdapter:
    def __init__(
        self,
        archive_path: str,
        executable: str,
        tool: str,
        cancel_check: Callable[[], bool],
    ):
        self.archive_path = os.path.abspath(archive_path)
        self.executable = executable
        self.tool = tool
        self.cancel_check = cancel_check
        self._signature = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def inventory(self) -> ArchiveInventory:
        try:
            archive_stat = os.stat(self.archive_path)
        except OSError as exc:
            raise ExtractionError(f"Cannot read archive: {self.archive_path}") from exc
        self._signature = (archive_stat.st_size, archive_stat.st_mtime_ns)

        if self.tool == "7z":
            records = _run_external(
                [
                    self.executable,
                    "l",
                    "-slt",
                    "-ba",
                    "-sccUTF-8",
                    "-p-",
                    "--",
                    self.archive_path,
                ],
                self.cancel_check,
                output_parser=_parse_slt_records,
            )
            members = self._members_from_7z(records, self.cancel_check)
        else:
            records = _run_external(
                [self.executable, "lt", "-c-", "-p-", self.archive_path],
                self.cancel_check,
                output_parser=_parse_colon_records,
            )
            members = self._members_from_unrar(records, self.cancel_check)
        if not members:
            raise ExtractionError("Archive tool returned no usable inventory.")
        return _validate_inventory(
            self.archive_path, members, self.cancel_check
        )

    @staticmethod
    def _members_from_7z(
        records: Sequence[dict[str, str]],
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[ArchiveMember]:
        members = []
        for record in records:
            _check_cancelled(cancel_check)
            path = record.get("Path")
            if not path:
                continue
            if record.get("Encrypted", "-") == "+":
                raise UnsafeArchiveError("Password-protected archives are not supported.")
            if (
                record.get("Symbolic Link")
                or record.get("Hard Link")
                or record.get("Reparse Point") == "+"
            ):
                raise UnsafeArchiveError(f"Archive contains a link: {path}")
            attributes = record.get("Attributes", "")
            if "REPARSE" in attributes.upper() or re.search(r"(?:^|\s)l[rwx-]", attributes):
                raise UnsafeArchiveError(f"Archive contains a link or reparse point: {path}")
            is_dir = record.get("Folder", "-") == "+" or attributes.upper().startswith("D")
            members.append(
                ArchiveMember(
                    path,
                    _integer(record, "Size"),
                    _integer(record, "Packed Size"),
                    is_dir,
                )
            )
        return members

    @staticmethod
    def _members_from_unrar(
        records: Sequence[dict[str, str]],
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[ArchiveMember]:
        members = []
        for record in records:
            _check_cancelled(cancel_check)
            path = record.get("Name")
            if not path:
                continue
            encrypted = record.get("Encrypted", "-").casefold()
            if encrypted not in ("", "-", "no"):
                raise UnsafeArchiveError("Password-protected archives are not supported.")
            entry_type = record.get("Type", "File").casefold()
            if "link" in entry_type or "junction" in entry_type:
                raise UnsafeArchiveError(f"Archive contains a link: {path}")
            is_dir = "directory" in entry_type or "folder" in entry_type
            if not is_dir and entry_type not in ("", "file"):
                raise UnsafeArchiveError(f"Archive contains an unsupported entry: {path}")
            members.append(
                ArchiveMember(
                    path,
                    _integer(record, "Size"),
                    _integer(record, "Packed Size", "Packed"),
                    is_dir,
                )
            )
        return members

    def extract(
        self,
        destination: str,
        inventory: ArchiveInventory,
        cancel_check: Callable[[], bool],
    ) -> None:
        try:
            current_stat = os.stat(self.archive_path)
        except OSError as exc:
            raise ExtractionError("Archive disappeared after inventory.") from exc
        if self._signature != (current_stat.st_size, current_stat.st_mtime_ns):
            raise UnsafeArchiveError("Archive changed after it was inventoried.")
        os.makedirs(destination, exist_ok=True)
        if self.tool == "7z":
            arguments = [
                self.executable,
                "x",
                "-y",
                "-bb0",
                "-bsp1",
                "-bso1",
                "-bse1",
                "-sccUTF-8",
                "-p-",
                f"-o{os.path.abspath(destination)}",
                "--",
                self.archive_path,
            ]
        else:
            arguments = [
                self.executable,
                "x",
                "-idc",
                "-o-",
                "-p-",
                self.archive_path,
                os.path.abspath(destination) + os.sep,
            ]
        _run_external(arguments, cancel_check, capture_output=False)
        _validate_extracted_tree(destination, inventory, cancel_check)


def _find_archive_tool(archive_path: str) -> tuple[str, str]:
    seven_zip = find_7z_executable()
    suffix = os.path.splitext(archive_path)[1].casefold()
    if seven_zip:
        return seven_zip, "7z"
    if suffix == ".rar":
        unrar = find_unrar_executable()
        if unrar:
            return unrar, "unrar"
    if suffix == ".7z":
        raise ArchiveToolUnavailable(
            "No suitable extractor found. Install 7-Zip and try again."
        )
    raise ArchiveToolUnavailable(
        "No suitable extractor found. Install 7-Zip or UnRAR and try again."
    )


def _open_adapter(
    archive_path: str,
    cancel_check: Callable[[], bool],
):
    suffix = os.path.splitext(archive_path)[1].casefold()
    if suffix == ".zip":
        return _ZipAdapter(archive_path, cancel_check)
    if suffix not in (".rar", ".7z"):
        raise ExtractionError(f"Unsupported archive type: {suffix or '<none>'}")
    executable, tool = _find_archive_tool(archive_path)
    return _ExternalAdapter(archive_path, executable, tool, cancel_check)


def _validate_extracted_tree(
    root: str,
    inventory: ArchiveInventory,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    expected = {
        member.path.casefold(): member
        for member in inventory.members
        if not member.is_dir
    }
    actual = {}
    root_abs = os.path.abspath(root)

    for current, directories, files in os.walk(root_abs, topdown=True, followlinks=False):
        _check_cancelled(cancel_check)
        for name in list(directories):
            _check_cancelled(cancel_check)
            path = os.path.join(current, name)
            if _is_link_or_reparse(path):
                raise UnsafeArchiveError(f"Extractor created a link or reparse point: {name}")
        for name in files:
            _check_cancelled(cancel_check)
            path = os.path.join(current, name)
            if _is_link_or_reparse(path):
                raise UnsafeArchiveError(f"Extractor created a link or reparse point: {name}")
            relative = os.path.relpath(path, root_abs).replace(os.sep, "/")
            normalised = _normalise_member_path(relative)
            key = normalised.casefold()
            if key in actual:
                raise UnsafeArchiveError(
                    f"Extractor created a case-insensitive collision: {normalised}"
                )
            actual[key] = (normalised, os.path.getsize(path))

    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))[:5]
        unexpected = sorted(set(actual) - set(expected))[:5]
        raise UnsafeArchiveError(
            f"Extracted members do not match inventory (missing={missing}, unexpected={unexpected})."
        )
    for key, (_, size) in actual.items():
        _check_cancelled(cancel_check)
        if size != expected[key].size:
            raise UnsafeArchiveError(
                f"Extracted size does not match inventory: {expected[key].path}"
            )


@dataclass
class _ExtractionBudget:
    entries: int = 0
    total_uncompressed: int = 0
    embedded_archives: int = 0

    def add(self, inventory: ArchiveInventory) -> None:
        self.entries += len(inventory.members)
        self.total_uncompressed += inventory.total_uncompressed
        self.embedded_archives += inventory.embedded_archives
        if self.entries > MAX_ARCHIVE_ENTRIES:
            raise UnsafeArchiveError("Import exceeds the 200,000 entry limit.")
        if self.total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise UnsafeArchiveError("Import exceeds the 100 GiB uncompressed size limit.")
        if self.embedded_archives > MAX_EMBEDDED_ARCHIVES:
            raise UnsafeArchiveError("Import contains more than 100 embedded archives.")


def inspect_archive(archive_path: str) -> ArchiveInventory:
    """Return a validated inventory without extracting the archive."""
    cancel_check = lambda: False
    with _open_adapter(archive_path, cancel_check) as adapter:
        return adapter.inventory()


def extract_archive_safely(
    archive_path: str,
    destination: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
    budget: _ExtractionBudget | None = None,
) -> ArchiveInventory:
    """Inventory and extract one archive exactly once into an empty directory."""
    cancel_check = cancel_check or (lambda: False)
    if cancel_check():
        raise ExtractionCancelled("Extraction cancelled.")
    if os.path.lexists(destination):
        if not os.path.isdir(destination) or os.listdir(destination):
            raise ExtractionError("Safe extraction destination must be an empty directory.")
    else:
        os.makedirs(destination, exist_ok=False)
    if _is_link_or_reparse(destination):
        raise UnsafeArchiveError("Extraction destination cannot be a link or reparse point.")

    with _open_adapter(archive_path, cancel_check) as adapter:
        inventory = adapter.inventory()
        if budget is not None:
            budget.add(inventory)
        _ensure_free_space(destination, inventory.total_uncompressed)
        adapter.extract(destination, inventory, cancel_check)
        return inventory


@dataclass
class _TreeAnalysis:
    content_files: list[tuple[str, str]] = field(default_factory=list)
    embedded_archives: list[str] = field(default_factory=list)
    template_archives: list[str] = field(default_factory=list)


def _scan_extracted_tree(
    root: str,
    daz_folders: set[str],
    cancel_check: Callable[[], bool] | None = None,
) -> _TreeAnalysis:
    analysis = _TreeAnalysis()
    output_paths = {}
    root_abs = os.path.abspath(root)

    for current, directories, files in os.walk(root_abs, topdown=True, followlinks=False):
        _check_cancelled(cancel_check)
        retained_directories = []
        for name in list(directories):
            _check_cancelled(cancel_check)
            path = os.path.join(current, name)
            if _is_link_or_reparse(path):
                raise UnsafeArchiveError(f"Extracted tree contains a link or reparse point: {path}")
            if name.casefold() not in _IGNORED_NAMES:
                retained_directories.append(name)
        directories[:] = retained_directories
        for name in files:
            _check_cancelled(cancel_check)
            source = os.path.join(current, name)
            if _is_link_or_reparse(source):
                raise UnsafeArchiveError(f"Extracted tree contains a link or reparse point: {source}")
            relative = os.path.relpath(source, root_abs).replace(os.sep, "/")
            normalised = _normalise_member_path(relative)
            parts = normalised.split("/")
            if any(part.casefold() in _IGNORED_NAMES for part in parts):
                continue
            if len(parts) == 1 and parts[0].casefold() in daz_folders:
                raise UnsafeArchiveError(
                    f"DAZ root must be a directory, not a file: {normalised}"
                )
            root_index = next(
                (
                    index
                    for index, part in enumerate(parts[:-1])
                    if part.casefold() in daz_folders
                ),
                None,
            )
            if root_index is not None:
                output_relative = "/".join(parts[root_index:])
                key = output_relative.casefold()
                previous = output_paths.get(key)
                if previous is not None:
                    raise UnsafeArchiveError(
                        f"DAZ content contains a case-insensitive collision: {previous} / {output_relative}"
                    )
                output_paths[key] = output_relative
                analysis.content_files.append((source, output_relative))
            elif _is_archive_name(normalised):
                if is_template_archive(normalised):
                    analysis.template_archives.append(source)
                else:
                    analysis.embedded_archives.append(source)

    return analysis


def _stage_content(
    analysis: _TreeAnalysis,
    destination: str,
    cancel_check: Callable[[], bool],
) -> None:
    required = sum(os.path.getsize(source) for source, _ in analysis.content_files)
    _ensure_free_space(destination, required)
    os.makedirs(destination, exist_ok=True)
    for source, relative in analysis.content_files:
        if cancel_check():
            raise ExtractionCancelled("Extraction cancelled.")
        target = _safe_destination(destination, relative)
        if os.path.lexists(target):
            raise UnsafeArchiveError(f"Content collision while staging: {relative}")
        _copy_file(source, target, cancel_check)


def _validate_template_source(template_path: str) -> str:
    template_path = os.path.abspath(template_path)
    name = os.path.basename(template_path)
    _normalise_member_path(name)
    if not _is_archive_name(name):
        raise ExtractionError(f"Unsupported template archive type: {name}")
    source_stat = os.lstat(template_path)
    if not stat.S_ISREG(source_stat.st_mode) or _is_link_or_reparse(template_path):
        raise UnsafeArchiveError(f"Template archive must be a regular file: {name}")
    if source_stat.st_size > MAX_FILE_UNCOMPRESSED_BYTES:
        raise UnsafeArchiveError(f"Template archive exceeds the 50 GiB limit: {name}")
    return template_path


@dataclass
class _PreparedArchive:
    content_root: str
    templates: list[str]


def _prepare_archive(
    archive_path: str,
    work_root: str,
    daz_folders: set[str],
    enable_template_detection: bool,
    cancel_check: Callable[[], bool],
    budget: _ExtractionBudget,
    allow_nested: bool = True,
    snapshot_source: bool = True,
) -> _PreparedArchive:
    source_archive = archive_path
    if snapshot_source:
        source_archive = _snapshot_archive(
            archive_path,
            os.path.join(work_root, "source_archive"),
            cancel_check,
        )
    outer_root = os.path.join(work_root, "outer")
    extract_archive_safely(
        source_archive,
        outer_root,
        cancel_check=cancel_check,
        budget=budget,
    )
    analysis = _scan_extracted_tree(outer_root, daz_folders, cancel_check)
    content_root = os.path.join(work_root, "content")
    templates = []

    if analysis.content_files:
        _stage_content(analysis, content_root, cancel_check)
    else:
        if not analysis.embedded_archives:
            raise ExtractionError("No recognized DAZ main folders found in the archive.")
        if not allow_nested:
            raise UnsafeArchiveError(
                f"Archive nesting exceeds the supported depth of {MAX_NESTING_DEPTH}."
            )
        if len(analysis.embedded_archives) != 1:
            raise ExtractionError(
                "Wrapper archive contains multiple content archives; select the package parts separately."
            )
        nested_root = os.path.join(work_root, "nested")
        extract_archive_safely(
            analysis.embedded_archives[0],
            nested_root,
            cancel_check=cancel_check,
            budget=budget,
        )
        nested_analysis = _scan_extracted_tree(
            nested_root, daz_folders, cancel_check
        )
        if not nested_analysis.content_files:
            if nested_analysis.embedded_archives:
                raise UnsafeArchiveError(
                    f"Archive nesting exceeds the supported depth of {MAX_NESTING_DEPTH}."
                )
            raise ExtractionError("No recognized DAZ main folders found in the embedded archive.")
        _stage_content(nested_analysis, content_root, cancel_check)
        if enable_template_detection:
            analysis.template_archives.extend(nested_analysis.template_archives)

    if enable_template_detection:
        seen_templates = set()
        for template_path in analysis.template_archives:
            name_key = os.path.basename(template_path).casefold()
            if name_key in seen_templates:
                raise UnsafeArchiveError(
                    f"Duplicate template archive name: {os.path.basename(template_path)}"
                )
            seen_templates.add(name_key)
            templates.append(_validate_template_source(template_path))

    return _PreparedArchive(content_root, templates)


@dataclass
class _TransactionEntry:
    source: str
    root: str
    relative: str
    containment_root: str | None = None


@dataclass
class _CommittedEntry:
    target: str
    backup: str | None


def _content_containment_root(content_dir: str) -> str:
    """Return the managed Builds root for a conventional build content path."""
    content_root = os.path.abspath(content_dir)
    build_root = os.path.dirname(content_root)
    if (
        os.path.basename(content_root).casefold() == "content"
        and re.fullmatch(r"Build\d+", os.path.basename(build_root), re.IGNORECASE)
    ):
        return os.path.dirname(build_root)
    return content_root


def _validate_containment_root(root: str, containment_root: str | None) -> None:
    if containment_root is None:
        return

    root_abs = os.path.abspath(root)
    boundary_abs = os.path.abspath(containment_root)
    try:
        common = os.path.commonpath((boundary_abs, root_abs))
    except ValueError as exc:
        raise UnsafeArchiveError(
            f"Extraction target escapes its containment root: {root_abs}"
        ) from exc
    if os.path.normcase(common) != os.path.normcase(boundary_abs):
        raise UnsafeArchiveError(
            f"Extraction target escapes its containment root: {root_abs}"
        )
    if not os.path.isdir(boundary_abs) or _is_link_or_reparse(boundary_abs):
        raise UnsafeArchiveError(
            f"Unsafe extraction containment root: {boundary_abs}"
        )

    relative = os.path.relpath(root_abs, boundary_abs)
    current = boundary_abs
    for part in (() if relative == "." else relative.split(os.sep)):
        current = os.path.join(current, part)
        if os.path.lexists(current) and _is_link_or_reparse(current):
            raise UnsafeArchiveError(
                "Extraction target contains a link or reparse point: "
                f"{current}"
            )


class _FileTransaction:
    def __init__(self, policy: ConflictPolicy | str):
        self.policy = ConflictPolicy.coerce(policy)
        self.entries: list[_TransactionEntry] = []
        self.skipped: list[str] = []
        self.applied: list[str] = []
        self._committed: list[_CommittedEntry] = []
        self._created_directories: list[str] = []
        self._transaction_dirs: dict[str, str] = {}

    def add_tree(
        self,
        source_root: str,
        target_root: str,
        cancel_check: Callable[[], bool] | None = None,
        *,
        containment_root: str | None = None,
    ) -> None:
        for current, directories, files in os.walk(source_root, topdown=True, followlinks=False):
            _check_cancelled(cancel_check)
            for name in directories:
                _check_cancelled(cancel_check)
                if _is_link_or_reparse(os.path.join(current, name)):
                    raise UnsafeArchiveError("Staged content contains a link or reparse point.")
            for name in files:
                _check_cancelled(cancel_check)
                source = os.path.join(current, name)
                relative = os.path.relpath(source, source_root).replace(os.sep, "/")
                self.entries.append(
                    _TransactionEntry(
                        source,
                        target_root,
                        relative,
                        containment_root,
                    )
                )

    def add_file(
        self,
        source: str,
        target_root: str,
        target_name: str | None = None,
        *,
        containment_root: str | None = None,
    ) -> None:
        self.entries.append(
            _TransactionEntry(
                source,
                target_root,
                target_name or os.path.basename(source),
                containment_root,
            )
        )

    @staticmethod
    def _resolve_case_path(
        root: str,
        relative: str,
        containment_root: str | None = None,
    ) -> tuple[str, bool]:
        _validate_containment_root(root, containment_root)
        root_abs = os.path.abspath(root)
        if os.path.lexists(root_abs):
            if not os.path.isdir(root_abs) or _is_link_or_reparse(root_abs):
                raise UnsafeArchiveError(f"Unsafe extraction target: {root_abs}")
        current = root_abs
        parts = _normalise_member_path(relative).split("/")
        for index, part in enumerate(parts):
            if not os.path.isdir(current):
                current = os.path.join(current, *parts[index:])
                return current, False
            matches = [name for name in os.listdir(current) if name.casefold() == part.casefold()]
            if len(matches) > 1:
                raise UnsafeArchiveError(f"Target contains a case-insensitive collision: {current}")
            actual = matches[0] if matches else part
            current = os.path.join(current, actual)
            if os.path.lexists(current) and _is_link_or_reparse(current):
                raise UnsafeArchiveError(f"Extraction target contains a link or reparse point: {current}")
        return current, os.path.lexists(current)

    def _planned(
        self,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[tuple[_TransactionEntry, str, bool]]:
        planned = []
        destinations = {}
        for entry in self.entries:
            _check_cancelled(cancel_check)
            target, exists = self._resolve_case_path(
                entry.root,
                entry.relative,
                entry.containment_root,
            )
            key = (os.path.abspath(entry.root).casefold(), entry.relative.casefold())
            if key in destinations:
                raise UnsafeArchiveError(f"Import contains a duplicate destination: {entry.relative}")
            destinations[key] = target
            if exists:
                if os.path.isdir(target):
                    raise ExtractionError(f"A directory blocks the imported file: {target}")
            planned.append((entry, target, exists))
        return planned

    def preflight_conflicts(
        self,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[str, ...]:
        """Return exact existing file targets without modifying the destination."""
        return tuple(
            target
            for _, target, exists in self._planned(cancel_check)
            if exists
        )

    def _ensure_directory(self, directory: str) -> None:
        missing = []
        current = os.path.abspath(directory)
        while not os.path.exists(current):
            missing.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        if os.path.exists(current) and (not os.path.isdir(current) or _is_link_or_reparse(current)):
            raise UnsafeArchiveError(f"Unsafe extraction target directory: {current}")
        for path in reversed(missing):
            os.mkdir(path)
            self._created_directories.append(path)

    def _transaction_directory(self, entry: _TransactionEntry) -> str:
        root = os.path.abspath(entry.root)
        key = os.path.normcase(root)
        existing = self._transaction_dirs.get(key)
        if existing is not None:
            return existing

        if entry.containment_root is not None:
            base = os.path.dirname(root)
            _validate_containment_root(base, entry.containment_root)
        else:
            base = root
        if not os.path.isdir(base) or _is_link_or_reparse(base):
            raise UnsafeArchiveError(
                f"Unsafe transaction directory location: {base}"
            )
        directory = tempfile.mkdtemp(prefix=".dimcreator-import-", dir=base)
        self._transaction_dirs[key] = directory
        return directory

    def _cleanup_transaction_directories(self) -> None:
        remaining = {}
        for key, directory in self._transaction_dirs.items():
            try:
                os.rmdir(directory)
            except FileNotFoundError:
                continue
            except OSError as exc:
                log.warning(
                    "Could not remove extraction transaction directory %s: %s",
                    directory,
                    exc,
                )
                remaining[key] = directory
        self._transaction_dirs = remaining

    @staticmethod
    def _publish_without_replace(temporary: str, target: str) -> None:
        """Publish a new file without overwriting a concurrently created target."""
        if os.name == "nt":
            os.rename(temporary, target)
            return
        os.link(temporary, target, follow_symlinks=False)
        os.remove(temporary)

    def commit(self, cancel_check: Callable[[], bool]) -> None:
        planned = self._planned(cancel_check)
        conflicts = [target for _, target, exists in planned if exists]
        if conflicts and self.policy is ConflictPolicy.CANCEL:
            preview = ", ".join(os.path.basename(path) for path in conflicts[:5])
            raise ExtractionConflict(
                f"Extraction cancelled because {len(conflicts)} file conflict(s) were found: {preview}"
            )
        required_by_volume: dict[tuple[str, int | str], tuple[str, int]] = {}
        for entry, _, exists in planned:
            if not (exists and self.policy is ConflictPolicy.SKIP):
                key = _volume_key(entry.root)
                probe, required = required_by_volume.get(key, (entry.root, 0))
                required_by_volume[key] = (
                    probe,
                    required + os.path.getsize(entry.source),
                )
        for root, required in required_by_volume.values():
            _ensure_free_space(root, required)

        try:
            for entry, _, _ in planned:
                if cancel_check():
                    raise ExtractionCancelled("Extraction cancelled.")
                target, exists = self._resolve_case_path(
                    entry.root,
                    entry.relative,
                    entry.containment_root,
                )
                if exists and os.path.isdir(target):
                    raise ExtractionError(
                        f"A directory blocks the imported file: {target}"
                    )
                if exists and self.policy is ConflictPolicy.CANCEL:
                    raise ExtractionConflict(
                        "Extraction cancelled because a file conflict appeared "
                        f"during commit: {os.path.basename(target)}"
                    )
                if exists and self.policy is ConflictPolicy.SKIP:
                    self.skipped.append(target)
                    continue
                self._ensure_directory(os.path.dirname(target))
                transaction_dir = self._transaction_directory(entry)
                temporary = os.path.join(
                    transaction_dir,
                    f"{uuid.uuid4().hex}.new",
                )
                backup = None
                committed_entry = None
                try:
                    _copy_file(entry.source, temporary, cancel_check)

                    # Keep the old file visible while the potentially large copy
                    # is prepared, then resolve the complete target chain again.
                    target, exists = self._resolve_case_path(
                        entry.root,
                        entry.relative,
                        entry.containment_root,
                    )
                    if exists and os.path.isdir(target):
                        raise ExtractionError(
                            f"A directory blocks the imported file: {target}"
                        )
                    if exists and self.policy is ConflictPolicy.CANCEL:
                        raise ExtractionConflict(
                            "Extraction cancelled because a file conflict appeared "
                            f"during commit: {os.path.basename(target)}"
                        )
                    if exists and self.policy is ConflictPolicy.SKIP:
                        os.remove(temporary)
                        self.skipped.append(target)
                        continue

                    if exists:
                        backup = os.path.join(
                            transaction_dir,
                            f"{uuid.uuid4().hex}.backup",
                        )
                        os.replace(target, backup)
                        committed_entry = _CommittedEntry(target, backup)
                        self._committed.append(committed_entry)
                        os.replace(temporary, target)
                    else:
                        try:
                            self._publish_without_replace(temporary, target)
                        except FileExistsError:
                            # Atomic no-clobber publication caught a target that
                            # appeared after the final path check.
                            target, exists = self._resolve_case_path(
                                entry.root,
                                entry.relative,
                                entry.containment_root,
                            )
                            if not exists:
                                raise
                            if os.path.isdir(target):
                                raise ExtractionError(
                                    f"A directory blocks the imported file: {target}"
                                )
                            if self.policy is ConflictPolicy.CANCEL:
                                raise ExtractionConflict(
                                    "Extraction cancelled because a file conflict "
                                    f"appeared during commit: {os.path.basename(target)}"
                                )
                            if self.policy is ConflictPolicy.SKIP:
                                os.remove(temporary)
                                self.skipped.append(target)
                                continue
                            backup = os.path.join(
                                transaction_dir,
                                f"{uuid.uuid4().hex}.backup",
                            )
                            os.replace(target, backup)
                            committed_entry = _CommittedEntry(target, backup)
                            self._committed.append(committed_entry)
                            os.replace(temporary, target)
                except BaseException:
                    if os.path.lexists(temporary):
                        os.remove(temporary)
                    raise
                if committed_entry is None:
                    self._committed.append(_CommittedEntry(target, None))
                self.applied.append(target)
        except BaseException as exc:
            try:
                self.rollback()
            except ExtractionRollbackError as rollback_exc:
                raise ExtractionRollbackError(
                    rollback_exc.failures,
                    original_error=exc,
                ) from exc
            raise

    @property
    def rollback_pending(self) -> bool:
        return bool(self._committed or self._created_directories)

    def rollback(self) -> None:
        failures: list[tuple[str, OSError]] = []
        pending_commits = []
        for committed in reversed(self._committed):
            try:
                if os.path.lexists(committed.target):
                    os.remove(committed.target)
                if committed.backup:
                    if not os.path.lexists(committed.backup):
                        raise FileNotFoundError(
                            f"Rollback backup is missing: {committed.backup}"
                        )
                    os.replace(committed.backup, committed.target)
            except OSError as exc:
                log.error("Failed to roll back extracted file %s: %s", committed.target, exc)
                failures.append((committed.target, exc))
                pending_commits.append(committed)
        self._committed = list(reversed(pending_commits))

        pending_directories = []
        for directory in reversed(self._created_directories):
            try:
                os.rmdir(directory)
            except FileNotFoundError:
                continue
            except OSError as exc:
                log.error(
                    "Failed to roll back extracted directory %s: %s",
                    directory,
                    exc,
                )
                failures.append((directory, exc))
                pending_directories.append(directory)
        self._created_directories = list(reversed(pending_directories))

        pending_targets = {
            os.path.normcase(os.path.abspath(committed.target))
            for committed in self._committed
        }
        self.applied = [
            path
            for path in self.applied
            if os.path.normcase(os.path.abspath(path)) in pending_targets
        ]
        self._cleanup_transaction_directories()
        if failures:
            raise ExtractionRollbackError(failures)

    def finalize(self) -> None:
        for committed in self._committed:
            if committed.backup:
                try:
                    os.remove(committed.backup)
                except OSError as exc:
                    log.warning("Failed to remove extraction backup %s: %s", committed.backup, exc)
        self._committed.clear()
        self._created_directories.clear()
        self._cleanup_transaction_directories()


def _user_error(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def _result_after_rollback(
    transaction: _FileTransaction,
    exc: BaseException,
    *,
    skipped_files: Sequence[str],
) -> ExtractionResult:
    original_error = (
        exc.original_error
        if isinstance(exc, ExtractionRollbackError)
        and exc.original_error is not None
        else exc
    )
    original_message = _user_error(original_error)
    try:
        transaction.rollback()
    except ExtractionRollbackError as rollback_exc:
        rollback_message = _user_error(rollback_exc)
        message = (
            f"{original_message} {rollback_message}. "
            "The rollback state was retained and can be retried."
        )
        return ExtractionResult(
            "error",
            message,
            skipped_files=list(skipped_files),
            errors=[original_message, rollback_message],
            _transaction=transaction,
        )

    if isinstance(exc, ExtractionRollbackError):
        log.warning("Extraction rollback completed successfully on retry.")
    status = (
        "cancelled"
        if isinstance(original_error, ExtractionCancelled)
        else "error"
    )
    return ExtractionResult(
        status,
        original_message,
        skipped_files=list(skipped_files),
        errors=[] if status == "cancelled" else [original_message],
    )


class _ConflictDecisionGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._response: ConflictPolicy | None = None
        self._waiting = False
        self._cancelled = False

    def resolve(self, policy: ConflictPolicy | str) -> None:
        response = ConflictPolicy.coerce(policy)
        with self._condition:
            if not self._waiting:
                return
            self._response = response
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def wait(
        self,
        conflicts: Sequence[str],
        emit: Callable[[object], None],
        cancel_check: Callable[[], bool],
    ) -> ConflictPolicy:
        with self._condition:
            self._response = None
            self._waiting = True
        try:
            emit(tuple(conflicts))
            with self._condition:
                while self._response is None:
                    if self._cancelled or cancel_check():
                        raise ExtractionCancelled("Extraction cancelled.")
                    self._condition.wait(timeout=0.1)
                return self._response
        finally:
            with self._condition:
                self._waiting = False


def _commit_with_optional_prompt(
    transaction: _FileTransaction,
    *,
    prompt_on_conflicts: bool,
    gate: _ConflictDecisionGate,
    emit_conflicts: Callable[[object], None],
    cancel_check: Callable[[], bool],
) -> None:
    if not prompt_on_conflicts:
        transaction.commit(cancel_check)
        return

    transaction.policy = ConflictPolicy.CANCEL
    conflicts = transaction.preflight_conflicts(cancel_check)
    prompted = False
    if conflicts:
        prompted = True
        policy = gate.wait(conflicts, emit_conflicts, cancel_check)
        if policy is ConflictPolicy.CANCEL:
            raise ExtractionCancelled("Extraction cancelled.")
        transaction.policy = policy

    try:
        transaction.commit(cancel_check)
    except ExtractionConflict:
        if prompted:
            raise
        conflicts = transaction.preflight_conflicts(cancel_check)
        if not conflicts:
            raise
        policy = gate.wait(conflicts, emit_conflicts, cancel_check)
        if policy is ConflictPolicy.CANCEL:
            raise ExtractionCancelled("Extraction cancelled.")
        transaction.policy = policy
        transaction.commit(cancel_check)


def _planned_archive(
    path: str,
    outer_root: str,
) -> PlannedEmbeddedArchive:
    relative = os.path.relpath(path, outer_root).replace(os.sep, "/")
    return PlannedEmbeddedArchive(
        _normalise_member_path(relative),
        os.path.abspath(path),
    )


def plan_archive_import(
    archive_path: str,
    daz_folders: Iterable[str],
    enable_template_detection: bool,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> ArchiveImportPlan:
    """Extract an outer archive once and retain its validated staging for UI selection."""
    cancel_check = cancel_check or (lambda: False)
    stage_root = tempfile.mkdtemp(prefix="dim_import_plan_")
    try:
        source_snapshot = _snapshot_archive(
            archive_path,
            os.path.join(stage_root, "source_archive"),
            cancel_check,
        )
        outer_root = os.path.join(stage_root, "outer")
        budget = _ExtractionBudget()
        extract_archive_safely(
            source_snapshot,
            outer_root,
            cancel_check=cancel_check,
            budget=budget,
        )
        folders = {str(folder).casefold() for folder in daz_folders}
        analysis = _scan_extracted_tree(outer_root, folders, cancel_check)
        direct_content_root = None
        warning = None

        template_items = [
            _planned_archive(path, outer_root)
            for path in analysis.template_archives
        ]
        embedded_items = [
            _planned_archive(path, outer_root)
            for path in analysis.embedded_archives
        ]
        ignored_items = []

        if analysis.content_files:
            direct_content_root = os.path.join(stage_root, "prepared_content")
            _stage_content(analysis, direct_content_root, cancel_check)
            # Archives outside a DAZ root are not package parts when direct content exists.
            ignored_items.extend(embedded_items)
            embedded_items = []
        elif embedded_items:
            if len(embedded_items) > 1:
                ordered_paths, warning = detect_heuristic_ordering(
                    [item.staged_path for item in embedded_items]
                )
                by_path = {
                    os.path.abspath(item.staged_path).casefold(): item
                    for item in embedded_items
                }
                if warning:
                    embedded_items = sorted(
                        embedded_items,
                        key=lambda item: item.relative_path.casefold(),
                    )
                else:
                    embedded_items = [
                        by_path[os.path.abspath(path).casefold()]
                        for path in ordered_paths
                    ]
        elif not template_items:
            raise ExtractionError("No recognized DAZ content or package archives were found.")

        if not enable_template_detection:
            ignored_items.extend(template_items)
            template_items = []

        plan = ArchiveImportPlan(
            stage_root=stage_root,
            direct_content_root=direct_content_root,
            content_archives=tuple(embedded_items),
            template_archives=tuple(
                sorted(template_items, key=lambda item: item.relative_path.casefold())
            ),
            ignored_archives=tuple(
                sorted(ignored_items, key=lambda item: item.relative_path.casefold())
            ),
            warning=warning,
            budget_entries=budget.entries,
            budget_uncompressed=budget.total_uncompressed,
            budget_embedded_archives=budget.embedded_archives,
        )
        plan.validate()
        return plan
    except BaseException:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise


class ArchivePlanningWorker(QThread):
    resultReady = Signal(object)

    def __init__(
        self,
        archive_path: str,
        daz_folders: Iterable[str],
        enable_template_detection: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.archive_path = archive_path
        self.daz_folders = tuple(str(folder) for folder in daz_folders)
        self.enable_template_detection = enable_template_detection

    def requestCancellation(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            plan = plan_archive_import(
                self.archive_path,
                self.daz_folders,
                self.enable_template_detection,
                cancel_check=self.isInterruptionRequested,
            )
            result = ArchivePlanningResult(
                "success", "Archive analysis completed successfully.", plan
            )
            self.resultReady.emit(result)
        except ExtractionCancelled as exc:
            result = ArchivePlanningResult("cancelled", _user_error(exc))
            self.resultReady.emit(result)
        except Exception as exc:
            log.exception("Archive planning failed")
            result = ArchivePlanningResult("error", _user_error(exc))
            self.resultReady.emit(result)


@dataclass(frozen=True)
class _BuildSnapshot:
    build_id: str
    folder: str
    part: int


@dataclass(frozen=True)
class _BuildTarget:
    part: int
    archive_path: str
    snapshot: _BuildSnapshot
    content_dir: str
    new_build: dict | None = None


def _snapshot_builds(session) -> tuple[_BuildSnapshot, ...]:
    builds = tuple(getattr(session, "builds", ()))
    return tuple(
        _BuildSnapshot(
            str(getattr(build, "id", "")),
            str(getattr(build, "folder", "")),
            int(getattr(build, "part", 0)),
        )
        for build in builds
    )


class ContentExtractionWorker(QThread):
    resultReady = Signal(object)
    conflictsDetected = Signal(object)

    def __init__(
        self,
        import_plan: ArchiveImportPlan,
        content_dir: str,
        template_destination: str | None,
        parent=None,
        *,
        conflict_policy: ConflictPolicy | str = ConflictPolicy.CANCEL,
        defer_finalize: bool = False,
        prompt_on_conflicts: bool = False,
    ):
        super().__init__(parent)
        if not isinstance(import_plan, ArchiveImportPlan):
            raise TypeError("ContentExtractionWorker requires an ArchiveImportPlan.")
        self.import_plan = import_plan
        self.content_dir = content_dir
        self.template_destination = template_destination or downloads_dir()
        self.conflict_policy = ConflictPolicy.coerce(conflict_policy)
        self.defer_finalize = bool(defer_finalize)
        self.prompt_on_conflicts = bool(prompt_on_conflicts)
        self._conflict_gate = _ConflictDecisionGate()

    def _cancelled(self) -> bool:
        return self.isInterruptionRequested()

    def requestCancellation(self) -> None:
        self.requestInterruption()
        self._conflict_gate.cancel()

    def resolveConflictPolicy(self, policy: ConflictPolicy | str) -> None:
        self._conflict_gate.resolve(policy)

    def run(self):
        transaction = _FileTransaction(self.conflict_policy)
        try:
            self.import_plan.claim()
            if not self.import_plan.is_direct_content:
                raise ExtractionError(
                    "Wrapper import plans must be committed with MultiBuildExtractionWorker."
                )
            transaction.add_tree(
                self.import_plan.direct_content_root,
                self.content_dir,
                self._cancelled,
                containment_root=_content_containment_root(self.content_dir),
            )
            template_targets = []
            for item in self.import_plan.template_archives:
                template = _validate_template_source(item.staged_path)
                transaction.add_file(template, self.template_destination)
                template_targets.append(
                    (
                        os.path.abspath(
                            os.path.join(
                                self.template_destination, os.path.basename(template)
                            )
                        ).casefold(),
                        os.path.basename(template),
                    )
                )
            _commit_with_optional_prompt(
                transaction,
                prompt_on_conflicts=self.prompt_on_conflicts,
                gate=self._conflict_gate,
                emit_conflicts=self.conflictsDetected.emit,
                cancel_check=self._cancelled,
            )
            applied = {os.path.abspath(path).casefold() for path in transaction.applied}
            copied_templates = [name for path, name in template_targets if path in applied]
            content_prefix = (
                os.path.abspath(self.content_dir).casefold().rstrip(os.sep) + os.sep
            )
            modified_builds = (
                [os.path.basename(os.path.dirname(self.content_dir))]
                if any(path.startswith(content_prefix) for path in applied)
                else []
            )
            if not self.defer_finalize:
                transaction.finalize()
            result = ExtractionResult(
                "success",
                "Extraction completed successfully.",
                modified_builds=modified_builds,
                copied_templates=copied_templates,
                skipped_files=list(transaction.skipped),
                _transaction=transaction if self.defer_finalize else None,
            )
            self.resultReady.emit(result)
        except ExtractionCancelled as exc:
            result = _result_after_rollback(
                transaction,
                exc,
                skipped_files=transaction.skipped,
            )
            self.resultReady.emit(result)
        except Exception as exc:
            log.exception("Extraction failed")
            result = _result_after_rollback(
                transaction,
                exc,
                skipped_files=transaction.skipped,
            )
            self.resultReady.emit(result)
        finally:
            self.import_plan.cleanup()


class MultiBuildExtractionWorker(QThread):
    extractionProgress = Signal(str)
    resultReady = Signal(object)
    conflictsDetected = Signal(object)

    def __init__(
        self,
        import_plan: ArchiveImportPlan,
        content_archives: Sequence[PlannedEmbeddedArchive],
        template_archives: Sequence[PlannedEmbeddedArchive],
        daz_folders: Iterable[str],
        session: Any,
        enable_template_detection: bool,
        template_destination: str | None,
        parent=None,
        *,
        conflict_policy: ConflictPolicy | str = ConflictPolicy.CANCEL,
        defer_finalize: bool = False,
        prompt_on_conflicts: bool = False,
    ):
        super().__init__(parent)
        if not isinstance(import_plan, ArchiveImportPlan):
            raise TypeError("MultiBuildExtractionWorker requires an ArchiveImportPlan.")
        content_archives = tuple(content_archives)
        template_archives = tuple(template_archives)
        if any(not isinstance(item, PlannedEmbeddedArchive) for item in content_archives):
            raise TypeError("Content selections must be PlannedEmbeddedArchive values.")
        if any(not isinstance(item, PlannedEmbeddedArchive) for item in template_archives):
            raise TypeError("Template selections must be PlannedEmbeddedArchive values.")
        self.content_archives = content_archives
        self.template_archives = template_archives
        self.import_plan = import_plan
        self.daz_folders = {str(folder).casefold() for folder in daz_folders}
        self.enable_template_detection = enable_template_detection
        self.template_destination = template_destination or downloads_dir()
        self.conflict_policy = ConflictPolicy.coerce(conflict_policy)
        self.defer_finalize = bool(defer_finalize)
        self.prompt_on_conflicts = bool(prompt_on_conflicts)
        self._conflict_gate = _ConflictDecisionGate()
        self._build_snapshots = _snapshot_builds(session)
        self._next_build_number = int(getattr(session, "next_build_number", 2))
        self.part_to_build = {build.part: build for build in self._build_snapshots}

    def _cancelled(self) -> bool:
        return self.isInterruptionRequested()

    def requestCancellation(self) -> None:
        self.requestInterruption()
        self._conflict_gate.cancel()

    def resolveConflictPolicy(self, policy: ConflictPolicy | str) -> None:
        self._conflict_gate.resolve(policy)

    def _claim_import_plan(self) -> None:
        self.import_plan.claim()
        if self.import_plan.is_direct_content:
            raise ExtractionError(
                "Direct-content import plans must be committed with ContentExtractionWorker."
            )
        if any(item not in self.import_plan.content_archives for item in self.content_archives):
            raise UnsafeArchiveError("Selected content archive is not part of the import plan.")
        if any(item not in self.import_plan.template_archives for item in self.template_archives):
            raise UnsafeArchiveError("Selected template archive is not part of the import plan.")

    def _allocate_builds(self):
        from utils import get_build_content_dir

        if len(self.content_archives) > 99 or len(self._build_snapshots) + sum(
            part not in self.part_to_build for part in range(1, len(self.content_archives) + 1)
        ) > 99:
            raise ExtractionError("A session can contain at most 99 builds.")

        used_ids = {build.build_id for build in self._build_snapshots}
        used_folders = {build.folder.casefold() for build in self._build_snapshots}
        next_number = self._next_build_number
        targets = []

        for part, archive in enumerate(self.content_archives, 1):
            snapshot = self.part_to_build.get(part)
            new_build = None
            if snapshot is None:
                while True:
                    if next_number > 99999:
                        raise ExtractionError("No valid build folder number is available.")
                    build_id = f"build_{next_number:03d}"
                    folder = f"Build{next_number:03d}"
                    if build_id not in used_ids and folder.casefold() not in used_folders:
                        break
                    next_number += 1
                guid = str(uuid.uuid4())
                snapshot = _BuildSnapshot(
                    build_id,
                    folder,
                    part,
                )
                new_build = {
                    "id": build_id,
                    "folder": folder,
                    "part": part,
                    "guid": guid,
                    "store": "",
                    "product_name": "",
                    "prefix": "",
                    "sku": "",
                    "tags": "DAZStudio4_5",
                    "image_path": "",
                    "content_status": "empty",
                    "overrides": {},
                    "checked": False,
                }
                used_ids.add(build_id)
                used_folders.add(folder.casefold())
                next_number += 1
            targets.append(
                _BuildTarget(
                    part,
                    archive.staged_path,
                    snapshot,
                    get_build_content_dir(snapshot.folder),
                    new_build,
                )
            )
        return targets, next_number

    def run(self):
        transaction = _FileTransaction(self.conflict_policy)
        try:
            self._claim_import_plan()
            # Validate the final dialog selection without changing the
            # user-confirmed build order.
            detect_heuristic_ordering(
                [item.staged_path for item in self.content_archives]
            )
            targets, next_number = self._allocate_builds()
            budget = self.import_plan.initial_budget()

            with tempfile.TemporaryDirectory(prefix="dim_multi_extract_") as work_root:
                template_targets = []
                for index, target in enumerate(targets, 1):
                    if self._cancelled():
                        raise ExtractionCancelled("Extraction cancelled.")
                    self.extractionProgress.emit(
                        f"Extracting Build {index}/{len(targets)}..."
                    )
                    prepared = _prepare_archive(
                        target.archive_path,
                        os.path.join(work_root, f"build_{target.part}"),
                        self.daz_folders,
                        self.enable_template_detection,
                        self._cancelled,
                        budget,
                        allow_nested=False,
                        snapshot_source=False,
                    )
                    transaction.add_tree(
                        prepared.content_root,
                        target.content_dir,
                        self._cancelled,
                        containment_root=_content_containment_root(
                            target.content_dir
                        ),
                    )
                    for template in prepared.templates:
                        transaction.add_file(template, self.template_destination)
                        template_targets.append(
                            (
                                os.path.abspath(
                                    os.path.join(
                                        self.template_destination,
                                        os.path.basename(template),
                                    )
                                ).casefold(),
                                os.path.basename(template),
                            )
                        )

                seen_templates = set()
                for item in self.template_archives:
                    if self._cancelled():
                        raise ExtractionCancelled("Extraction cancelled.")
                    template_path = _validate_template_source(item.staged_path)
                    key = os.path.basename(template_path).casefold()
                    if key in seen_templates:
                        raise UnsafeArchiveError(
                            f"Duplicate template archive name: {os.path.basename(template_path)}"
                    )
                    seen_templates.add(key)
                    transaction.add_file(template_path, self.template_destination)
                    template_targets.append(
                        (
                            os.path.abspath(
                                os.path.join(
                                    self.template_destination,
                                    os.path.basename(template_path),
                                )
                            ).casefold(),
                            os.path.basename(template_path),
                        )
                    )

                _commit_with_optional_prompt(
                    transaction,
                    prompt_on_conflicts=self.prompt_on_conflicts,
                    gate=self._conflict_gate,
                    emit_conflicts=self.conflictsDetected.emit,
                    cancel_check=self._cancelled,
                )
                applied_paths = [os.path.abspath(path).casefold() for path in transaction.applied]
                modified_builds = []
                for target in targets:
                    content_abs = os.path.abspath(target.content_dir).casefold()
                    if any(
                        path.startswith(content_abs + os.sep.casefold())
                        for path in applied_paths
                    ):
                        modified_builds.append(target.snapshot.folder)

                applied = set(applied_paths)
                copied_templates = [
                    name for path, name in template_targets if path in applied
                ]
                if not self.defer_finalize:
                    transaction.finalize()

            result = ExtractionResult(
                "success",
                "Extraction completed successfully.",
                modified_builds=modified_builds,
                copied_templates=copied_templates,
                skipped_files=list(transaction.skipped),
                new_builds=[
                    dict(target.new_build)
                    for target in targets
                    if target.new_build is not None
                ],
                next_build_number=next_number,
                _transaction=transaction if self.defer_finalize else None,
            )
            self.resultReady.emit(result)
        except ExtractionCancelled as exc:
            result = _result_after_rollback(
                transaction,
                exc,
                skipped_files=transaction.skipped,
            )
            self.resultReady.emit(result)
        except Exception as exc:
            log.exception("Multi-build extraction failed")
            result = _result_after_rollback(
                transaction,
                exc,
                skipped_files=transaction.skipped,
            )
            self.resultReady.emit(result)
        finally:
            self.import_plan.cleanup()
