import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any
from logger_utils import get_logger

log = get_logger(__name__)


def _get_utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + 'Z'


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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Build':
        build_data = dict(data)

        overrides = build_data.get('overrides')
        if overrides is None:
            build_data['overrides'] = {}
        elif not isinstance(overrides, dict):
            build_data['overrides'] = {}

        required_fields = ('id', 'folder', 'part', 'guid')
        missing = [field for field in required_fields
                   if field not in build_data or build_data[field] is None]
        if missing:
            raise ValueError(
                f"Build data is missing required field(s): {', '.join(missing)}"
            )

        part = build_data['part']
        if not isinstance(part, int):
            raise ValueError("Build 'part' must be an integer")
        if part < 1:
            raise ValueError("Build 'part' must be >= 1")

        for key in ('id', 'folder'):
            value = build_data[key]
            if not isinstance(value, str):
                raise ValueError(f"Build '{key}' must be a string")
            if not value.strip():
                raise ValueError(f"Build '{key}' must be a non-empty string")

        guid = build_data['guid']
        if not isinstance(guid, str):
            raise ValueError("Build 'guid' must be a string")
        if not guid.strip():
            raise ValueError("Build 'guid' must be a non-empty string")
        try:
            uuid.UUID(guid)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("Build 'guid' must be a valid UUID string")

        return cls(**build_data)


@dataclass
class Session:
    version: int = 1
    created_at: str = ""
    last_saved: str = ""
    last_destination: str = ""
    last_selected_build: int = 0
    next_build_number: int = 2
    builds: list[Build] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _get_utc_timestamp()
        if not self.last_saved:
            self.last_saved = self.created_at

    def to_dict(self) -> dict:
        return {
            'version': self.version,
            'created_at': self.created_at,
            'last_saved': self.last_saved,
            'last_destination': self.last_destination,
            'last_selected_build': self.last_selected_build,
            'next_build_number': self.next_build_number,
            'builds': [build.to_dict() for build in self.builds]
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Session':
        builds_data = data.get('builds', [])
        builds = [Build.from_dict(build_data) for build_data in builds_data]

        legacy_last_selected = data.get('last_selected_part')
        last_selected_build = data.get('last_selected_build', legacy_last_selected)

        session_kwargs = {
            key: value
            for key, value in data.items()
            if key not in ('builds', 'last_selected_part', 'last_selected_build')
        }
        if last_selected_build is not None:
            session_kwargs['last_selected_build'] = last_selected_build

        session = cls(**session_kwargs)
        session.builds = builds
        return session


def save_session(session: Session, path: str) -> None:
    session.last_saved = _get_utc_timestamp()

    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(session.to_dict(), f, indent=2, ensure_ascii=False)
    except (OSError, IOError) as e:
        log.error(f"Failed to save session to {path}: {e}")
        raise IOError(f"Failed to save session to {path}: {e}") from e


def load_session(path: str) -> Optional[Session]:
    try:
        if not Path(path).exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return Session.from_dict(data)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning(f"Failed to load session from {path}: {e}")
        return None


def create_default_session() -> Session:
    build_guid = str(uuid.uuid4())

    build_001 = Build(
        id="build_001",
        folder="Build001",
        part=1,
        guid=build_guid,
        tags="DAZStudio4_5",
        content_status="empty"
    )

    session = Session(
        next_build_number=2,
        builds=[build_001]
    )

    return session
