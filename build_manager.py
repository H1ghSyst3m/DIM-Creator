import os
import uuid
from typing import Optional, Any
from session import Session, Build
from utils import (
    create_build_folder, 
    delete_build_folder
)
from logger_utils import get_logger

log = get_logger(__name__)


def create_build(session: Session) -> Build:
    if not (1 <= session.next_build_number <= 99999):
        raise ValueError(f"Build number {session.next_build_number} is out of valid range (1-99999)")
    
    build_id = f"build_{session.next_build_number:03d}"
    folder_name = f"Build{session.next_build_number:03d}"
    build_guid = str(uuid.uuid4())
    
    if session.builds:
        max_build = max(build.part for build in session.builds)
        build_number = max_build + 1
    else:
        build_number = 1
    
    if build_number == 1:
        new_build = Build(
            id=build_id,
            folder=folder_name,
            part=build_number,
            guid=build_guid,
            tags="DAZStudio4_5",
            content_status="empty"
        )
    else:
        new_build = Build(
            id=build_id,
            folder=folder_name,
            part=build_number,
            guid=build_guid,
            content_status="empty",
            overrides={}
        )
    
    create_build_folder(folder_name)
    session.builds.append(new_build)
    session.next_build_number += 1
    
    return new_build


def delete_build(session: Session, build_id: str) -> Session:
    build_to_delete = None
    for build in session.builds:
        if build.id == build_id:
            build_to_delete = build
            break
    
    if not build_to_delete:
        return session
    
    delete_build_folder(build_to_delete.folder)
    session.builds.remove(build_to_delete)
    
    if not session.builds:
        create_build(session)
        return session
    
    if build_to_delete.part == 1:
        new_part_1 = session.builds[0]
        
        for field_name in ['store', 'product_name', 'prefix', 'sku', 'tags', 'image_path']:
            if field_name in new_part_1.overrides:
                setattr(new_part_1, field_name, new_part_1.overrides[field_name])
            else:
                setattr(new_part_1, field_name, getattr(build_to_delete, field_name))
        
        new_part_1.overrides = {}
    
    for i, build in enumerate(session.builds, start=1):
        build.part = i
    
    return session


def sync_to_children(session: Session, field: Optional[str] = None) -> None:
    if not session.builds:
        return
    
    synced_fields = ['store', 'product_name', 'prefix', 'sku', 'tags', 'image_path']
    if field:
        fields_to_sync = [field] if field in synced_fields else []
    else:
        fields_to_sync = synced_fields
    
    for build in session.builds[1:]:
        for field_name in fields_to_sync:
            if field_name in build.overrides:
                del build.overrides[field_name]


def sync_from_parent(session: Session, build_id: str) -> None:
    target_build = None
    for build in session.builds:
        if build.id == build_id:
            target_build = build
            break
    
    if not target_build or target_build.part == 1:
        return
    
    target_build.overrides = {}


def get_effective_value(session: Session, build: Build, field: str) -> Any:
    if build.part == 1:
        return getattr(build, field, "")
    
    if field in build.overrides:
        return build.overrides[field]
    
    if session.builds:
        part_1 = next((b for b in session.builds if getattr(b, "part", None) == 1), None)
        if part_1 is not None:
            return getattr(part_1, field, "")
    
    return ""


def get_build_data(session: Session, build: Build) -> dict[str, Any]:
    synced_fields = ['store', 'product_name', 'prefix', 'sku', 'tags', 'image_path']
    
    data = {
        'id': build.id,
        'folder': build.folder,
        'part': build.part,
        'guid': build.guid,
        'content_status': build.content_status
    }
    
    for field in synced_fields:
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
        
        for field_name in ['store', 'product_name', 'prefix', 'sku', 'tags', 'image_path']:
            if field_name in new_part_1.overrides:
                setattr(new_part_1, field_name, new_part_1.overrides[field_name])
            else:
                setattr(new_part_1, field_name, getattr(old_part_1, field_name))
        
        new_part_1.overrides = {}
        
        for field_name in ['store', 'product_name', 'prefix', 'sku', 'tags', 'image_path']:
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
            content_items = os.listdir(content_dir)
            for item in content_items:
                if item.casefold() in daz_folders_lower:
                    item_path = os.path.join(content_dir, item)
                    if os.path.isdir(item_path):
                        has_content = True
                        break
        except OSError as e:
            log.warning("Failed to list contents of '%s' while validating build '%s': %s",
                        content_dir, getattr(build, "id", "<unknown>"), e)

    if not has_content:
        return "empty"
    
    if effective_values is not None and not isinstance(effective_values, dict):
        raise TypeError("effective_values must be a dictionary or None")
    
    required_fields = ['store', 'product_name', 'prefix', 'sku']
    for field in required_fields:
        if effective_values and field in effective_values:
            value = effective_values[field]
        else:
            value = getattr(build, field, "")
        if not isinstance(value, str) or not value.strip():
            return "incomplete"
    
    if not build.guid or len(build.guid) < 32:
        return "incomplete"
    
    return "ready"


def _get_build1_build(session: Session) -> Optional[Build]:
    if hasattr(session, "builds") and session.builds:
        for b in session.builds:
            if getattr(b, "part", None) == 1:
                return b
    return None


def set_field_override(session: Session, build: Build, field: str, value: Any) -> None:
    synced_fields = ['store', 'product_name', 'prefix', 'sku', 'tags', 'image_path']
    
    if build.part == 1:
        setattr(build, field, value)
    elif field in synced_fields:
        build1_build = _get_build1_build(session)
        
        if build1_build is not None:
            base_value = getattr(build1_build, field, None)
        else:
            log.warning(f"Build 1 not found in session when setting field '{field}' for Build {build.part}. "
                       f"Using current build's value as fallback.")
            base_value = getattr(build, field, None)
        
        if value == base_value:
            if field in build.overrides:
                del build.overrides[field]
        else:
            build.overrides[field] = value
    else:
        setattr(build, field, value)


