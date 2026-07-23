import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from logger_utils import get_logger


log = get_logger(__name__)

SESSION_SCHEMA_VERSION = 2
MAX_BUILDS = 99
MAX_SESSION_BACKUPS = 10

_BUILD_ID_RE = re.compile(r"^build_(\d{3,5})$")
_BUILD_FOLDER_RE = re.compile(r"^Build(\d{3,5})$")
SYNCED_BUILD_FIELDS = (
    "store", "product_name", "prefix", "sku", "tags", "image_path"
)
_BUILD_FIELDS = frozenset(
    {
        "id",
        "folder",
        "part",
        "guid",
        "store",
        "product_name",
        "prefix",
        "sku",
        "tags",
        "image_path",
        "content_status",
        "overrides",
        "checked",
    }
)
_VALID_CONTENT_STATUSES = frozenset({"empty", "incomplete", "ready"})


class SessionError(ValueError):
    """Base error for invalid or unsafe session persistence."""


class UnsupportedSessionVersionError(SessionError):
    """Raised when a session belongs to a newer application version."""


class SessionRecoveryError(SessionError):
    """Raised when a corrupt session cannot be recovered safely."""


class SessionSaveError(OSError):
    """Raised when a session cannot be saved without risking existing data."""


def _get_utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def _unique_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _require_string(value: Any, field_name: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise SessionError(f"'{field_name}' must be a string")
    if not allow_empty and not value.strip():
        raise SessionError(f"'{field_name}' must be a non-empty string")
    return value


@dataclass
class Build:
    id: str
    folder: str
    part: int
    guid: str
    store: str = ""
    product_name: str = ""
    prefix: str = ""
    sku: str = ""
    tags: str = "DAZStudio4_5"
    image_path: str = ""
    content_status: str = "empty"
    overrides: dict[str, Any] = field(default_factory=dict)
    checked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Build":
        if not isinstance(data, dict):
            raise SessionError("Each build must be a JSON object")

        build_data = {key: value for key, value in data.items() if key in _BUILD_FIELDS}
        for required in ("id", "folder", "part", "guid"):
            if required not in build_data:
                raise SessionError(f"Build is missing required field '{required}'")

        build_id = _require_string(build_data["id"], "build.id", allow_empty=False)
        folder = _require_string(build_data["folder"], "build.folder", allow_empty=False)
        id_match = _BUILD_ID_RE.fullmatch(build_id)
        folder_match = _BUILD_FOLDER_RE.fullmatch(folder)
        if not id_match:
            raise SessionError("Build 'id' must match 'build_001' through 'build_99999'")
        if not folder_match:
            raise SessionError("Build 'folder' must match 'Build001' through 'Build99999'")
        if not 1 <= int(id_match.group(1)) <= 99999:
            raise SessionError("Build number must be between 1 and 99999")
        if id_match.group(1) != folder_match.group(1):
            raise SessionError("Build 'id' and 'folder' must use the same numeric suffix")

        part = build_data["part"]
        if isinstance(part, bool) or not isinstance(part, int):
            raise SessionError("Build 'part' must be an integer")
        if not 1 <= part <= MAX_BUILDS:
            raise SessionError(f"Build 'part' must be between 1 and {MAX_BUILDS}")

        guid_value = _require_string(build_data["guid"], "build.guid", allow_empty=False)
        try:
            guid = str(uuid.UUID(guid_value))
        except (ValueError, AttributeError, TypeError) as exc:
            raise SessionError("Build 'guid' must be a valid UUID string") from exc

        string_defaults = {
            "store": "",
            "product_name": "",
            "prefix": "",
            "sku": "",
            "tags": "DAZStudio4_5",
            "image_path": "",
            "content_status": "empty",
        }
        normalized: dict[str, Any] = {
            "id": build_id,
            "folder": folder,
            "part": part,
            "guid": guid,
        }
        for key, default in string_defaults.items():
            normalized[key] = _require_string(build_data.get(key, default), f"build.{key}")

        if normalized["content_status"] not in _VALID_CONTENT_STATUSES:
            normalized["content_status"] = "empty"

        raw_overrides = build_data.get("overrides", {})
        if raw_overrides is None:
            raw_overrides = {}
        if not isinstance(raw_overrides, dict):
            raise SessionError("Build 'overrides' must be an object")
        overrides: dict[str, str] = {}
        for key, value in raw_overrides.items():
            if key not in SYNCED_BUILD_FIELDS:
                continue
            overrides[key] = _require_string(value, f"build.overrides.{key}")
        normalized["overrides"] = overrides

        checked = build_data.get("checked", False)
        if not isinstance(checked, bool):
            raise SessionError("Build 'checked' must be a boolean")
        normalized["checked"] = checked
        return cls(**normalized)


@dataclass
class Session:
    version: int = SESSION_SCHEMA_VERSION
    created_at: str = ""
    last_saved: str = ""
    last_destination: str = ""
    last_selected_build_id: str = ""
    next_build_number: int = 2
    builds: list[Build] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _get_utc_timestamp()
        if not self.last_saved:
            self.last_saved = self.created_at
        if self.builds and not self.last_selected_build_id:
            self.last_selected_build_id = self.builds[0].id

    def recalculate_derived_fields(self) -> None:
        numbers = []
        for build in self.builds:
            match = _BUILD_ID_RE.fullmatch(build.id)
            if match:
                numbers.append(int(match.group(1)))
        self.next_build_number = max(numbers, default=0) + 1
        if self.next_build_number < 2:
            self.next_build_number = 2
        if self.builds and not any(
            build.id == self.last_selected_build_id for build in self.builds
        ):
            self.last_selected_build_id = self.builds[0].id

    def validate(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise SessionError("Session 'version' must be an integer")
        if self.version > SESSION_SCHEMA_VERSION:
            raise UnsupportedSessionVersionError(
                f"Session schema v{self.version} is newer than supported v{SESSION_SCHEMA_VERSION}"
            )
        if self.version < 1:
            raise SessionError("Session 'version' must be at least 1")
        if not isinstance(self.builds, list) or not 1 <= len(self.builds) <= MAX_BUILDS:
            raise SessionError(f"Session must contain between 1 and {MAX_BUILDS} builds")

        ids: set[str] = set()
        folders: set[str] = set()
        expected_parts = list(range(1, len(self.builds) + 1))
        actual_parts: list[int] = []
        for build in self.builds:
            if not isinstance(build, Build):
                raise SessionError("Session builds must be Build objects")
            normalized = Build.from_dict(build.to_dict())
            if normalized.id.casefold() in ids:
                raise SessionError(f"Duplicate build id: {normalized.id}")
            if normalized.folder.casefold() in folders:
                raise SessionError(f"Duplicate build folder: {normalized.folder}")
            ids.add(normalized.id.casefold())
            folders.add(normalized.folder.casefold())
            actual_parts.append(normalized.part)
        if actual_parts != expected_parts:
            raise SessionError(
                f"Build parts must be unique and ordered from 1 to {len(self.builds)}"
            )

        _require_string(self.created_at, "created_at", allow_empty=False)
        _require_string(self.last_saved, "last_saved", allow_empty=False)
        _require_string(self.last_destination, "last_destination")
        _require_string(self.last_selected_build_id, "last_selected_build_id", allow_empty=False)
        if self.last_selected_build_id.casefold() not in ids:
            raise SessionError("Selected build ID does not exist in the session")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SESSION_SCHEMA_VERSION,
            "created_at": self.created_at,
            "last_saved": self.last_saved,
            "last_destination": self.last_destination,
            "last_selected_build_id": self.last_selected_build_id,
            "next_build_number": self.next_build_number,
            "builds": [build.to_dict() for build in self.builds],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        if not isinstance(data, dict):
            raise SessionError("Session root must be a JSON object")

        version = data.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int):
            raise SessionError("Session 'version' must be an integer")
        if version > SESSION_SCHEMA_VERSION:
            raise UnsupportedSessionVersionError(
                f"Session schema v{version} is newer than supported v{SESSION_SCHEMA_VERSION}"
            )
        if version < 1:
            raise SessionError("Session 'version' must be at least 1")

        builds_data = data.get("builds", [])
        if not isinstance(builds_data, list):
            raise SessionError("Session 'builds' must be a list")
        if not 1 <= len(builds_data) <= MAX_BUILDS:
            raise SessionError(f"Session must contain between 1 and {MAX_BUILDS} builds")
        builds = [Build.from_dict(build_data) for build_data in builds_data]

        created_at = data.get("created_at") or _get_utc_timestamp()
        last_saved = data.get("last_saved") or created_at
        last_destination = data.get("last_destination", "")
        _require_string(created_at, "created_at", allow_empty=False)
        _require_string(last_saved, "last_saved", allow_empty=False)
        _require_string(last_destination, "last_destination")

        selected_id = ""
        if version >= 2:
            candidate = data.get("last_selected_build_id", "")
            if candidate:
                selected_id = _require_string(candidate, "last_selected_build_id")
        else:
            if "last_selected_build" in data:
                legacy = data.get("last_selected_build")
                if isinstance(legacy, int) and not isinstance(legacy, bool):
                    if 0 <= legacy < len(builds):
                        selected_id = builds[legacy].id
            elif "last_selected_part" in data:
                legacy_part = data.get("last_selected_part")
                if isinstance(legacy_part, int) and not isinstance(legacy_part, bool):
                    selected = next(
                        (build for build in builds if build.part == legacy_part), None
                    )
                    if selected is not None:
                        selected_id = selected.id
        if not selected_id or not any(build.id == selected_id for build in builds):
            selected_id = builds[0].id

        session = cls(
            version=SESSION_SCHEMA_VERSION,
            created_at=created_at,
            last_saved=last_saved,
            last_destination=last_destination,
            last_selected_build_id=selected_id,
            builds=builds,
        )
        session.recalculate_derived_fields()
        session.validate()
        return session


@dataclass(frozen=True)
class SessionLoadResult:
    session: Optional[Session]
    source: Literal["primary", "backup", "new"]
    warning: str = ""


def _read_session_file(path: Path) -> Session:
    with path.open("r", encoding="utf-8") as handle:
        return Session.from_dict(json.load(handle))


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())

        with temp_path.open("r", encoding="utf-8") as handle:
            verified = json.load(handle)
        Session.from_dict(verified)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _backup_path_for(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_stat = backup_dir.lstat()
    if (
        not stat.S_ISDIR(backup_stat.st_mode)
        or backup_dir.is_symlink()
        or _is_reparse_point(backup_stat)
    ):
        raise OSError(f"Session backup directory is unsafe: {backup_dir}")
    return backup_dir / f"{path.stem}_{_unique_stamp()}_{uuid.uuid4().hex}.json"


def _create_backup(path: Path) -> Path:
    destination = _backup_path_for(path)
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with path.open("rb") as source, temp_path.open("xb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, destination)
        _prune_backups(path)
        return destination
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _prune_backups(path: Path) -> None:
    try:
        backups = _backup_candidates(path)
        for backup in backups[MAX_SESSION_BACKUPS:]:
            try:
                backup.unlink()
            except OSError as exc:
                log.warning("Could not remove old session backup %s: %s", backup, exc)
    except OSError as exc:
        log.warning(
            "Could not prune session backups in %s: %s",
            path.parent / "backups",
            exc,
        )


def _quarantine(path: Path) -> Path:
    quarantine = path.with_name(
        f"{path.stem}.corrupt-{_unique_stamp()}-{uuid.uuid4().hex}{path.suffix}"
    )
    os.replace(path, quarantine)
    return quarantine


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(path_stat, "st_file_attributes", 0) & flag)


def _backup_candidates(path: Path, *, strict: bool = False) -> list[Path]:
    backup_dir = path.parent / "backups"
    try:
        backup_stat = backup_dir.lstat()
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        log.warning("Cannot inspect session backup directory %s", backup_dir)
        return []

    if (
        not stat.S_ISDIR(backup_stat.st_mode)
        or backup_dir.is_symlink()
        or _is_reparse_point(backup_stat)
    ):
        message = f"Session backup directory is unsafe: {backup_dir}"
        if strict:
            raise OSError(message)
        log.warning(message)
        return []

    candidates: list[tuple[int, Path]] = []
    for item in backup_dir.glob(f"{path.stem}_*.json"):
        try:
            item_stat = item.lstat()
        except OSError as exc:
            if strict:
                raise
            log.warning("Cannot inspect session backup %s: %s", item, exc)
            continue
        if (
            not stat.S_ISREG(item_stat.st_mode)
            or item.is_symlink()
            or _is_reparse_point(item_stat)
        ):
            if strict:
                raise OSError(f"Session backup is not a regular file: {item}")
            log.warning("Ignoring unsafe session backup %s", item)
            continue
        candidates.append((item_stat.st_mtime_ns, item))
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    return [item for _, item in candidates]


def _valid_backups(path: Path) -> list[tuple[Path, Session]]:
    valid: list[tuple[Path, Session]] = []
    for candidate in _backup_candidates(path):
        try:
            valid.append((candidate, _read_session_file(candidate)))
        except (OSError, UnicodeError, json.JSONDecodeError, SessionError, TypeError) as exc:
            log.warning("Ignoring invalid session backup %s: %s", candidate, exc)
    return valid


def delete_session_artifacts(
    path: str | os.PathLike[str],
    *,
    include_backups: bool = False,
) -> None:
    target = Path(path)
    backups = _backup_candidates(target, strict=True) if include_backups else []

    if target.exists() or target.is_symlink():
        target_stat = target.lstat()
        if (
            not stat.S_ISREG(target_stat.st_mode)
            or target.is_symlink()
            or _is_reparse_point(target_stat)
        ):
            raise OSError(f"Session file is not a regular file: {target}")

    for backup in backups:
        backup.unlink()
    if target.exists():
        target.unlink()


def save_session(session: Session, path: str) -> None:
    target = Path(path)
    previous_last_saved = session.last_saved
    try:
        if isinstance(session.version, bool) or not isinstance(session.version, int):
            raise SessionError("Session 'version' must be an integer")
        if session.version > SESSION_SCHEMA_VERSION:
            raise UnsupportedSessionVersionError(
                f"Session schema v{session.version} is newer than supported v{SESSION_SCHEMA_VERSION}"
            )
        session.version = SESSION_SCHEMA_VERSION
        session.recalculate_derived_fields()
        session.validate()

        if target.exists():
            try:
                existing = _read_session_file(target)
            except UnsupportedSessionVersionError:
                raise
            except (OSError, UnicodeError, json.JSONDecodeError, SessionError, TypeError) as exc:
                raise SessionSaveError(
                    f"Refusing to overwrite invalid session '{target}': {exc}"
                ) from exc
            if existing.version > SESSION_SCHEMA_VERSION:
                raise UnsupportedSessionVersionError(
                    f"Session schema v{existing.version} is newer than supported v{SESSION_SCHEMA_VERSION}"
                )

        new_timestamp = _get_utc_timestamp()
        session.last_saved = new_timestamp
        payload = Session.from_dict(session.to_dict()).to_dict()
        if target.exists():
            _create_backup(target)
        _atomic_write_json(target, payload)
    except (OSError, UnicodeError, json.JSONDecodeError, SessionError, TypeError) as exc:
        session.last_saved = previous_last_saved
        log.error("Failed to save session to %s: %s", target, exc)
        if isinstance(exc, (SessionError, SessionSaveError)):
            raise
        raise SessionSaveError(f"Failed to save session to {target}: {exc}") from exc


def load_session_result(path: str) -> SessionLoadResult:
    target = Path(path)
    if target.exists():
        try:
            return SessionLoadResult(_read_session_file(target), "primary")
        except UnsupportedSessionVersionError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, SessionError, TypeError) as exc:
            try:
                quarantined = _quarantine(target)
            except OSError as quarantine_error:
                raise SessionRecoveryError(
                    f"Session is invalid and could not be quarantined: {quarantine_error}"
                ) from quarantine_error
            warning = f"Invalid session was quarantined as '{quarantined.name}': {exc}"
            log.warning(warning)

            backups = _valid_backups(target)
            if not backups:
                raise SessionRecoveryError(
                    warning + "; no valid backup is available"
                ) from exc
            backup_path, recovered = backups[0]
            _atomic_write_json(target, recovered.to_dict())
            return SessionLoadResult(
                recovered,
                "backup",
                warning + f"; restored '{backup_path.name}'",
            )

    backups = _valid_backups(target)
    if backups:
        backup_path, recovered = backups[0]
        _atomic_write_json(target, recovered.to_dict())
        return SessionLoadResult(
            recovered,
            "backup",
            f"Primary session was missing; restored '{backup_path.name}'",
        )
    return SessionLoadResult(None, "new")


def create_default_session() -> Session:
    build = Build(
        id="build_001",
        folder="Build001",
        part=1,
        guid=str(uuid.uuid4()),
        tags="DAZStudio4_5",
        content_status="empty",
    )
    return Session(builds=[build], last_selected_build_id=build.id)
