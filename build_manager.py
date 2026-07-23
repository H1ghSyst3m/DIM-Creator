import os
import uuid
from typing import Optional, Any

from naming_utils import DIM_PREFIX_PATTERN
from session import Build, MAX_BUILDS, SYNCED_BUILD_FIELDS, Session
from utils import (
    create_build_folder,
    delete_build_folder,
    has_reparse_point,
)
from logger_utils import get_logger

log = get_logger(__name__)


def create_build(session: Session) -> Build:
    if len(session.builds) >= MAX_BUILDS:
        raise ValueError(f"A session cannot contain more than {MAX_BUILDS} builds")

    session.recalculate_derived_fields()
    if not (1 <= session.next_build_number <= 99999):
        raise ValueError(
            f"Build number {session.next_build_number} is out of valid range (1-99999)"
        )
    
    build_id = f"build_{session.next_build_number:03d}"
    folder_name = f"Build{session.next_build_number:03d}"
    build_number = len(session.builds) + 1
    new_build = Build(
        id=build_id,
        folder=folder_name,
        part=build_number,
        guid=str(uuid.uuid4()),
    )
    
    create_build_folder(folder_name)
    session.builds.append(new_build)
    session.next_build_number += 1
    if not any(
        build.id == session.last_selected_build_id for build in session.builds
    ):
        session.last_selected_build_id = new_build.id
    
    return new_build


def _parent_build(session: Session) -> Optional[Build]:
    return next((build for build in session.builds if build.part == 1), None)


def _promote_to_parent(build: Build, previous_parent: Build) -> None:
    for field_name in SYNCED_BUILD_FIELDS:
        value = build.overrides.get(
            field_name, getattr(previous_parent, field_name)
        )
        setattr(build, field_name, value)
    build.overrides.clear()


def delete_build(session: Session, build_id: str) -> Session:
    build_to_delete = next(
        (build for build in session.builds if build.id == build_id), None
    )
    if build_to_delete is None:
        return session
    
    delete_build_folder(build_to_delete.folder)
    session.builds.remove(build_to_delete)
    
    if not session.builds:
        session.last_selected_build_id = ""
        new_build = create_build(session)
        session.last_selected_build_id = new_build.id
        return session
    
    if build_to_delete.part == 1:
        new_part_1 = session.builds[0]
        _promote_to_parent(new_part_1, build_to_delete)
    
    for i, build in enumerate(session.builds, start=1):
        build.part = i

    if session.last_selected_build_id == build_id:
        session.last_selected_build_id = session.builds[0].id
    session.recalculate_derived_fields()
    
    return session


def sync_to_children(session: Session, field: Optional[str] = None) -> None:
    if not session.builds:
        return
    
    if field:
        fields_to_sync = (field,) if field in SYNCED_BUILD_FIELDS else ()
    else:
        fields_to_sync = SYNCED_BUILD_FIELDS
    
    for build in session.builds[1:]:
        for field_name in fields_to_sync:
            build.overrides.pop(field_name, None)


def sync_from_parent(session: Session, build_id: str) -> None:
    target_build = next((build for build in session.builds if build.id == build_id), None)
    if not target_build or target_build.part == 1:
        return
    
    target_build.overrides = {}


def get_effective_value(session: Session, build: Build, field: str) -> Any:
    if build.part == 1:
        return getattr(build, field, "")
    
    if field in build.overrides:
        return build.overrides[field]
    
    parent = _parent_build(session)
    if parent is not None:
        return getattr(parent, field, "")
    
    return ""


def get_build_data(session: Session, build: Build) -> dict[str, Any]:
    data = {
        'id': build.id,
        'folder': build.folder,
        'part': build.part,
        'guid': build.guid,
        'content_status': build.content_status
    }
    
    for field in SYNCED_BUILD_FIELDS:
        data[field] = get_effective_value(session, build, field)
    
    return data


def reorder_builds(session: Session, new_order: list[str]) -> None:
    build_map = {build.id: build for build in session.builds}
    
    existing_ids = set(build_map.keys())
    new_order_ids = set(new_order)
    
    if existing_ids != new_order_ids:
        missing = existing_ids - new_order_ids
        extra = new_order_ids - existing_ids
        error_parts = []
        if missing:
            error_parts.append(f"missing: {sorted(missing)}")
        if extra:
            error_parts.append(f"extra: {sorted(extra)}")
        raise ValueError(f"new_order must contain exactly the same builds as session ({', '.join(error_parts)})")
    
    old_part_1 = session.builds[0] if session.builds else None
    session.builds = [build_map[build_id] for build_id in new_order]
    
    if session.builds and old_part_1 and session.builds[0].id != old_part_1.id:
        new_part_1 = session.builds[0]
        _promote_to_parent(new_part_1, old_part_1)
        
        for field_name in SYNCED_BUILD_FIELDS:
            old_value = getattr(old_part_1, field_name)
            new_value = getattr(new_part_1, field_name)
            if old_value != new_value:
                old_part_1.overrides[field_name] = old_value
    
    for i, build in enumerate(session.builds, start=1):
        build.part = i


def validate_build(build: Build, content_dir: str, daz_folders: list[str], 
                   effective_values: Optional[dict[str, Any]] = None) -> str:
    """Returns 'ready', 'incomplete', or 'empty'."""
    has_content = False
    if os.path.exists(content_dir):
        try:
            daz_folders_lower = {folder.casefold() for folder in daz_folders}
            for item in os.listdir(content_dir):
                if item.casefold() in daz_folders_lower:
                    item_path = os.path.join(content_dir, item)
                    if not os.path.isdir(item_path) or has_reparse_point(item_path):
                        continue
                    for root, dirnames, filenames in os.walk(item_path, followlinks=False):
                        dirnames[:] = [
                            name for name in dirnames
                            if not has_reparse_point(os.path.join(root, name))
                        ]
                        if any(
                            name.casefold() not in {'.ds_store', 'thumbs.db', 'desktop.ini'}
                            and not has_reparse_point(os.path.join(root, name))
                            for name in filenames
                        ):
                            has_content = True
                            break
                    if has_content:
                        break
        except OSError as e:
            log.warning("Failed to list contents of '%s' while validating build '%s': %s",
                        content_dir, getattr(build, "id", "<unknown>"), e)

    if not has_content:
        return "empty"
    
    if effective_values is not None and not isinstance(effective_values, dict):
        raise TypeError("effective_values must be a dictionary or None")
    
    required_fields = ['store', 'product_name', 'prefix', 'sku']
    values: dict[str, Any] = {}
    for field in required_fields:
        if effective_values and field in effective_values:
            value = effective_values[field]
        else:
            value = getattr(build, field, "")
        if not isinstance(value, str) or not value.strip():
            return "incomplete"
        values[field] = value.strip()

    if DIM_PREFIX_PATTERN.fullmatch(values['prefix']) is None:
        return "incomplete"
    if not values['sku'].isdigit() or not 1 <= int(values['sku']) <= 99999999:
        return "incomplete"

    tags = effective_values.get('tags', build.tags) if effective_values else build.tags
    if not isinstance(tags, str):
        return "incomplete"
    selected_tags = {tag.strip().casefold() for tag in tags.split(',') if tag.strip()}
    if selected_tags & {'plugin', 'software'}:
        return "incomplete"
    
    try:
        uuid.UUID(build.guid)
    except (ValueError, AttributeError, TypeError):
        return "incomplete"
    
    return "ready"


def set_field_override(session: Session, build: Build, field: str, value: Any) -> None:
    if field not in SYNCED_BUILD_FIELDS:
        raise ValueError(f"Unsupported synchronized build field: {field}")
    if not isinstance(value, str):
        raise TypeError(f"Build field '{field}' must be a string")
    
    if build.part == 1:
        setattr(build, field, value)
        return

    parent = _parent_build(session)
    if parent is not None:
        base_value = getattr(parent, field, None)
    else:
        log.warning(
            "Build 1 not found in session when setting field '%s' for Build %s. "
            "Using current build's value as fallback.",
            field,
            build.part,
        )
        base_value = getattr(build, field, None)

    if value == base_value:
        build.overrides.pop(field, None)
    else:
        build.overrides[field] = value


