import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from logger_utils import get_logger
from naming_utils import DAZ_RESERVED_PREFIXES, DIM_PREFIX_PATTERN
from version import CONFIG_VERSION


log = get_logger(__name__)

CURRENT_CONFIG_VERSION = max(CONFIG_VERSION, 2)
MAX_CONFIG_BACKUPS = 10
_UNSUPPORTED_CONTENT_TAGS = frozenset({"plugin", "software"})


class ConfigError(ValueError):
    pass


class UnsupportedConfigVersionError(ConfigError):
    pass


def is_valid_dim_prefix(prefix: str) -> bool:
    return isinstance(prefix, str) and DIM_PREFIX_PATTERN.fullmatch(prefix) is not None


def _unique_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _backup_path_for(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
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
    backup_dir = path.parent / "backups"
    try:
        backups = sorted(
            backup_dir.glob(f"{path.stem}_*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        for backup in backups[MAX_CONFIG_BACKUPS:]:
            try:
                backup.unlink()
            except OSError as exc:
                log.warning("Could not remove old config backup %s: %s", backup, exc)
    except OSError as exc:
        log.warning("Could not prune config backups in %s: %s", backup_dir, exc)


def atomic_write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    """Write JSON transactionally and preserve the previous file in a unique backup."""
    if not isinstance(payload, dict):
        raise ConfigError("Configuration root must be a JSON object")
    payload_version = payload.get("version", 0)
    if isinstance(payload_version, bool) or not isinstance(payload_version, int):
        raise ConfigError("Configuration version must be an integer")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            with target.open("r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(
                f"Refusing to overwrite invalid configuration '{target}': {exc}"
            ) from exc
        if not isinstance(existing, dict):
            raise ConfigError(
                f"Refusing to overwrite invalid configuration '{target}': root is not an object"
            )
        existing_version = existing.get("version", 0)
        if isinstance(existing_version, bool) or not isinstance(existing_version, int):
            raise ConfigError(
                f"Refusing to overwrite invalid configuration '{target}': invalid version"
            )
        if existing_version > payload_version:
            raise UnsupportedConfigVersionError(
                f"Configuration v{existing_version} is newer than v{payload_version}"
            )

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=4, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())

        with temp_path.open("r", encoding="utf-8") as handle:
            verified = json.load(handle)
        if verified != payload:
            raise ConfigError(f"Verification failed while writing '{target}'")

        if target.exists():
            _create_backup(target)
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _quarantine_config(path: Path) -> Path:
    destination = path.with_name(
        f"{path.stem}.corrupt-{_unique_stamp()}-{uuid.uuid4().hex}{path.suffix}"
    )
    os.replace(path, destination)
    return destination


def _load_latest_config_backup(
    path: Path, current_version: int
) -> tuple[Path, dict[str, Any]] | None:
    backup_dir = path.parent / "backups"
    if not backup_dir.is_dir():
        return None
    candidates = sorted(
        backup_dir.glob(f"{path.stem}_*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for candidate in candidates:
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                continue
            version = data.get("version", 0)
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version > current_version
            ):
                continue
            return candidate, data
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            continue
    return None


def normalize_store_items(
    items: Iterable[Any], *, reject_invalid: bool = False
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            if reject_invalid:
                raise ConfigError("Every store must be an object")
            continue
        name = item.get("name", "")
        prefix = item.get("prefix", "")
        if not isinstance(name, str) or not name.strip():
            if reject_invalid:
                raise ConfigError("Every store needs a name")
            continue
        if not isinstance(prefix, str):
            if reject_invalid:
                raise ConfigError(f"Prefix for '{name}' must be text")
            prefix = ""

        name = name.strip()
        prefix = prefix.strip().upper()
        key = name.casefold()
        if key in seen:
            if reject_invalid:
                raise ConfigError(f"Duplicate store name: {name}")
            log.warning("Duplicate store name '%s' ignored (keeping first)", name)
            continue
        seen.add(key)

        if key == "3dexport" and prefix == "3DX":
            prefix = "D3X"
        if key == "local user" and prefix == "IM":
            prefix = "LOCAL"

        if not is_valid_dim_prefix(prefix):
            message = (
                f"Prefix '{prefix}' for '{name}' must match "
                "[A-Z][A-Z0-9]{0,6}"
            )
            if reject_invalid:
                raise ConfigError(message)
            log.warning(message)
        elif prefix in DAZ_RESERVED_PREFIXES and key != "daz 3d":
            log.warning(
                "Store '%s' uses DAZ's reserved %s prefix; choose a vendor prefix",
                name, prefix,
            )
        normalized.append({"name": name, "prefix": prefix})
    return normalized


def normalize_tag_items(
    items: Iterable[Any], *, reject_unsupported: bool = False
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            if reject_unsupported:
                raise ConfigError("Tags must be non-empty text values")
            continue
        value = item.strip()
        if value.casefold() == "lightwave":
            value = "Lightwave"
        key = value.casefold()
        if key in _UNSUPPORTED_CONTENT_TAGS:
            if reject_unsupported:
                raise ConfigError(
                    f"'{value}' is not supported for Content packages"
                )
            log.warning("Removing unsupported Content package tag '%s'", value)
            continue
        if key not in seen:
            seen.add(key)
            normalized.append(value)
    return normalized


def _normalize_simple_items(items: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        value = item.strip()
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _merge_ordered(existing: list[Any], defaults: list[Any], is_dict: bool, path: Path) -> list[Any]:
    if is_dict:
        current = normalize_store_items(existing)
        additions = normalize_store_items(defaults)
        default_by_name = {item["name"].casefold(): item for item in additions}
        for item in current:
            default_item = default_by_name.get(item["name"].casefold())
            if default_item and not is_valid_dim_prefix(item["prefix"]):
                item["prefix"] = default_item["prefix"]
        present = {item["name"].casefold() for item in current}
        current.extend(
            item for item in additions if item["name"].casefold() not in present
        )
        return current

    if path.name.casefold() == "product_tags.json":
        current_tags = normalize_tag_items(existing)
        default_tags = normalize_tag_items(defaults)
        present = {item.casefold() for item in current_tags}
        current_tags.extend(item for item in default_tags if item.casefold() not in present)
        return current_tags

    current_values = _normalize_simple_items(existing)
    default_values = _normalize_simple_items(defaults)
    present = {item.casefold() for item in current_values}
    current_values.extend(item for item in default_values if item.casefold() not in present)
    return current_values


def update_configuration(
    config_path: str,
    default_data: Dict[str, Any],
    current_version: int,
    is_dict: bool = True,
):
    path = Path(config_path)
    raw: dict[str, Any]
    needs_write = False

    if path.exists():
        log.debug("Loading configuration: %s", path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ConfigError("Configuration root must be an object")
            version = loaded.get("version", 0)
            if isinstance(version, bool) or not isinstance(version, int):
                raise ConfigError("Configuration version must be an integer")
            if version > current_version:
                log.warning(
                    "Configuration %s is v%s; this app supports v%s and will not overwrite it",
                    path,
                    version,
                    current_version,
                )
                data = loaded.get("data", [])
                if not isinstance(data, list):
                    return []
                if is_dict:
                    return normalize_store_items(data)
                if path.name.casefold() == "product_tags.json":
                    return normalize_tag_items(data)
                return _normalize_simple_items(data)
            raw = loaded
        except UnsupportedConfigVersionError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ConfigError, TypeError) as exc:
            try:
                quarantined = _quarantine_config(path)
            except OSError as quarantine_error:
                raise ConfigError(
                    f"Invalid configuration could not be quarantined: {quarantine_error}"
                ) from quarantine_error
            log.warning(
                "Invalid configuration %s quarantined as %s: %s",
                path,
                quarantined,
                exc,
            )
            recovered = _load_latest_config_backup(path, current_version)
            if recovered is None:
                raw = {"version": 0, "data": []}
            else:
                backup_path, raw = recovered
                log.warning("Restoring configuration backup %s", backup_path)
            needs_write = True
    else:
        raw = {"version": 0, "data": []}
        needs_write = True

    raw_data = raw.get("data", [])
    if not isinstance(raw_data, list):
        raw_data = []
        needs_write = True
    default_items = default_data.get("data", [])
    if not isinstance(default_items, list):
        raise ConfigError("Default configuration data must be a list")

    raw_version = raw.get("version", 0)
    merge_defaults = raw_version < current_version
    merged = _merge_ordered(
        raw_data,
        default_items if merge_defaults else [],
        is_dict,
        path,
    )
    if merged != raw_data or raw_version != current_version:
        needs_write = True
    result = {"version": current_version, "data": merged}

    if needs_write:
        atomic_write_json(path, result)
        log.info(
            "Wrote configuration %s (version=%s, items=%s)",
            path,
            current_version,
            len(merged),
        )
    return merged


def load_configurations(doc_main_dir: str) -> Tuple[List[str], Dict[str, str], List[str], List[str]]:
    config_version = CURRENT_CONFIG_VERSION
    config_path = Path(doc_main_dir) / "Config"
    config_path.mkdir(parents=True, exist_ok=True)

    default_store_data = {
        "version": config_version,
        "data": [
            {"name": "DAZ 3D", "prefix": "IM"},
            {"name": "Renderosity", "prefix": "RO"},
            {"name": "Renderhub", "prefix": "RH"},
            {"name": "Renderotica", "prefix": "RE"},
            {"name": "CGBytes", "prefix": "CB"},
            {"name": "CGTrader", "prefix": "CG"},
            {"name": "DeviantArt", "prefix": "DA"},
            {"name": "ShareCG", "prefix": "SH"},
            {"name": "Sketchfab", "prefix": "SF"},
            {"name": "Free3D", "prefix": "F3D"},
            {"name": "Turbosquid", "prefix": "TS"},
            {"name": "3DExport", "prefix": "D3X"},
            {"name": "Patreon", "prefix": "PR"},
            {"name": "Forender", "prefix": "FR"},
            {"name": "LOCAL USER", "prefix": "LOCAL"},
        ],
    }

    default_tags = {
        "version": config_version,
        "data": [
            "3dsMax",
            "Blender",
            "Bryce",
            "CarraraLegacy",
            "Carrara7",
            "Carrara7_2",
            "Carrara8",
            "Carrara8_5",
            "Cinema4D",
            "CloudAvailable",
            "CloudInstalled",
            "DAZStudioLegacy",
            "DAZStudio3",
            "DAZStudio4",
            "DAZStudio4_5",
            "DAZStudio5",
            "DSON_Poser",
            "General",
            "Hexagon",
            "InstallManager",
            "Lightwave",
            "Mac32",
            "Mac64",
            "Maya",
            "Photoshop",
            "PoserLegacy",
            "Poser9",
            "PrivateBuild",
            "PublicBuild",
            "PublishingBuild",
            "Unity",
            "Unreal",
            "Vue",
            "Win32",
            "Win64",
        ],
    }

    default_daz_folders = {
        "version": config_version,
        "data": [
            "aniBlocks",
            "data",
            "Environments",
            "General",
            "Light Presets",
            "People",
            "Props",
            "Render Presets",
            "Render Settings",
            "Runtime",
            "Scene Builder",
            "Scenes",
            "Scripts",
            "Shader Presets",
            "Shaders",
        ],
    }

    store_items = update_configuration(
        str(config_path / "store_data.json"), default_store_data, config_version, True
    )
    tag_items = update_configuration(
        str(config_path / "product_tags.json"), default_tags, config_version, False
    )
    daz_folder_items = update_configuration(
        str(config_path / "daz_folders.json"), default_daz_folders, config_version, False
    )

    store_names = [item["name"] for item in store_items if isinstance(item, dict)]
    store_prefixes = {
        item["name"]: item.get("prefix", "")
        for item in store_items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    tag_items = sorted(
        (item for item in tag_items if isinstance(item, str)), key=str.casefold
    )
    daz_folder_items = sorted(
        (item for item in daz_folder_items if isinstance(item, str)), key=str.casefold
    )

    log.info(
        "Configurations loaded: stores=%d, tags=%d, daz_folders=%d",
        len(store_names),
        len(tag_items),
        len(daz_folder_items),
    )
    return store_names, store_prefixes, tag_items, daz_folder_items
