from __future__ import annotations

import os
import io
import queue
import re
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional
from xml.dom import minidom
from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement, tostring

from PIL import Image, ImageOps
from PySide6.QtCore import QThread, Signal

from logger_utils import get_logger
from naming_utils import (
    build_product_store_idx,
    build_dim_zip_filename,
    build_support_cover_filename,
    validate_dim_zip_filename,
    validate_dim_part,
    validate_dim_prefix,
    validate_dim_sku,
)
from utils import WINDOWS_RESERVED_NAMES, find_7z_executable, hidden_subprocess_kwargs

log = get_logger(__name__)

SEVEN_ZIP_TOTAL_TIMEOUT_SECONDS = 4 * 60 * 60
SEVEN_ZIP_PROGRESS_TIMEOUT_SECONDS = 5 * 60
COPY_CHUNK_SIZE = 1024 * 1024
MAX_COVER_BYTES = 20 * 1024 * 1024
MAX_COVER_PIXELS = 40_000_000
SYSTEM_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "__macosx"}
BUILD_DIRECTORY_NAME = re.compile(r"Build\d+", re.IGNORECASE)
INTERNAL_ARTIFACT_NAME = re.compile(
    r"^(?:\.dimcreator-|\..+\.dim-(?:backup|new)-[0-9a-f]{32}$)",
    re.IGNORECASE,
)


def prettify(elem: Element) -> str:
    rough_string = tostring(elem, "utf-8")
    reparsed = minidom.parseString(rough_string)
    pretty = reparsed.toprettyxml(indent="  ")
    return "\n".join(pretty.split("\n")[1:])


class PackageStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class PackageResult:
    status: PackageStatus
    message: str
    final_path: Optional[str] = None
    file_size: int = 0
    cancelled: bool = False

    @property
    def success(self) -> bool:
        return self.status == PackageStatus.SUCCESS

@dataclass(frozen=True)
class PackageInventoryEntry:
    source_path: str
    archive_path: str
    size: int

    @property
    def manifest_value(self) -> Optional[str]:
        if self.archive_path.startswith("Content/"):
            return self.archive_path
        return None


@dataclass(frozen=True)
class PackageInventory:
    entries: tuple[PackageInventoryEntry, ...]

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def archive_members(self) -> tuple[str, ...]:
        return tuple(entry.archive_path for entry in self.entries)

    @property
    def manifest_members(self) -> tuple[str, ...]:
        return tuple(
            value
            for entry in self.entries
            if (value := entry.manifest_value) is not None
        )

    def with_entries(
        self,
        additional_entries: Iterable[PackageInventoryEntry],
    ) -> PackageInventory:
        combined = [*self.entries, *additional_entries]
        seen: dict[str, str] = {}
        for entry in combined:
            key = entry.archive_path.casefold()
            previous = seen.get(key)
            if previous is not None:
                raise PackagingError(
                    "Package inventory contains a case-insensitive name collision: "
                    f"{previous!r} and {entry.archive_path!r}."
                )
            seen[key] = entry.archive_path
        combined.sort(key=lambda entry: (entry.archive_path.casefold(), entry.archive_path))
        return PackageInventory(tuple(combined))

    @classmethod
    def from_content(
        cls,
        content_dir: str | os.PathLike[str],
        *,
        clean_support: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PackageInventory:
        root = Path(content_dir)
        return cls(
            _inventory_entries(
                root,
                archive_prefix="Content",
                clean_support=clean_support,
                cancel_check=cancel_check,
            )
        )

@dataclass
class PackageSpec:
    content_dir: str
    store: str
    product_name: str
    prefix: str
    sku: str | int
    product_part: str | int
    product_tags: str
    image_path: Optional[str]
    clean_support: bool
    guid: str
    destination_folder: str
    recognized_content_roots: Optional[tuple[str, ...]] = None
    replace_existing: bool = False


class PackagingError(RuntimeError):
    pass


class PackagingCancelled(PackagingError):
    pass


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(path_stat, "st_file_attributes", 0) & flag)


def _path_is_link_or_reparse(path: str | os.PathLike[str]) -> bool:
    path_stat = Path(path).lstat()
    return stat.S_ISLNK(path_stat.st_mode) or _is_reparse_point(path_stat)


def _managed_builds_root(content_dir: Path) -> Path | None:
    build_root = content_dir.parent
    if (
        content_dir.name.casefold() == "content"
        and BUILD_DIRECTORY_NAME.fullmatch(build_root.name)
    ):
        return build_root.parent
    return None


def _validate_build_source_chain(content_dir: Path) -> None:
    builds_root = _managed_builds_root(content_dir)
    if builds_root is None:
        return
    for candidate in (builds_root, content_dir.parent, content_dir):
        if _path_is_link_or_reparse(candidate):
            raise PackagingError(
                "Content directory path cannot contain a link or reparse point: "
                f"{candidate}"
            )


def _validate_archive_segment(segment: str) -> None:
    if not segment or segment in {".", ".."}:
        raise PackagingError("Package content contains an invalid path segment.")
    if any(ord(char) < 32 for char in segment):
        raise PackagingError(f"Package content contains control characters: {segment!r}")
    if any(char in '<>:"/\\|?*' for char in segment):
        raise PackagingError(f"Package content is not a valid Windows path: {segment!r}")
    if segment.endswith((" ", ".")):
        raise PackagingError(f"Package content has an unsafe trailing character: {segment!r}")
    if segment.split(".", 1)[0].rstrip(" .").upper() in WINDOWS_RESERVED_NAMES:
        raise PackagingError(f"Package content uses a reserved Windows name: {segment!r}")


def _inventory_entries(
    root: Path,
    *,
    archive_prefix: str,
    clean_support: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[PackageInventoryEntry, ...]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise PackagingError(f"Cannot access package content: {exc}") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_point(root_stat):
        raise PackagingError("Package content must be a regular directory.")

    entries: list[PackageInventoryEntry] = []
    seen: dict[str, str] = {}

    def register(relative: PurePosixPath, kind: str) -> None:
        archive_path = "/".join(filter(None, (archive_prefix, relative.as_posix())))
        key = archive_path.casefold()
        previous = seen.get(key)
        identity = f"{kind}:{archive_path}"
        if previous is not None and previous != identity:
            previous_path = previous.split(":", 1)[1]
            raise PackagingError(
                "Package content contains a case-insensitive name collision: "
                f"{previous_path!r} and {archive_path!r}."
            )
        seen[key] = identity

    def scan(directory: Path, relative_dir: PurePosixPath) -> None:
        try:
            children = sorted(
                os.scandir(directory),
                key=lambda entry: (entry.name.casefold(), entry.name),
            )
        except OSError as exc:
            raise PackagingError(f"Cannot read package content directory: {exc}") from exc

        for child in children:
            if cancel_check is not None and cancel_check():
                raise PackagingCancelled("Cancelled by user")
            name = child.name
            if name.casefold() in SYSTEM_NAMES or INTERNAL_ARTIFACT_NAME.match(name):
                continue
            _validate_archive_segment(name)
            relative = relative_dir / name
            if clean_support:
                parts = tuple(part.casefold() for part in relative.parts)
                if len(parts) >= 2 and parts[:2] == ("runtime", "support"):
                    continue
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise PackagingError(
                    f"Cannot inspect package content {child.path!r}: {exc}"
                ) from exc
            if child.is_symlink() or _is_reparse_point(child_stat):
                continue
            if stat.S_ISDIR(child_stat.st_mode):
                register(relative, "directory")
                scan(Path(child.path), relative)
            elif stat.S_ISREG(child_stat.st_mode):
                register(relative, "file")
                archive_path = "/".join(filter(None, (archive_prefix, relative.as_posix())))
                entries.append(
                    PackageInventoryEntry(
                        source_path=child.path,
                        archive_path=archive_path,
                        size=child_stat.st_size,
                    )
                )

    scan(root, PurePosixPath())
    entries.sort(key=lambda entry: (entry.archive_path.casefold(), entry.archive_path))
    return tuple(entries)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _is_build_tree_destination(destination: Path, build_root: Path) -> bool:
    """Return whether a package target can overwrite any managed build tree."""
    if _is_within(destination, build_root):
        return True
    if not BUILD_DIRECTORY_NAME.fullmatch(build_root.name):
        return False
    return _is_within(destination, build_root.parent)


def _validated_package_paths(
    content_folder: str | os.PathLike,
    destination_folder: str | os.PathLike,
) -> tuple[Path, Path]:
    try:
        content_input = Path(content_folder)
        input_stat = content_input.lstat()
    except (OSError, TypeError) as exc:
        raise PackagingError(f"Cannot access Content directory: {exc}") from exc
    if content_input.is_symlink() or _is_reparse_point(input_stat):
        raise PackagingError("Content directory cannot be a link or reparse point.")
    _validate_build_source_chain(Path(os.path.abspath(content_input)))
    try:
        content_dir = content_input.resolve(strict=True)
    except OSError as exc:
        raise PackagingError(f"Cannot access Content directory: {exc}") from exc
    if not content_dir.is_dir():
        raise PackagingError("Content directory does not exist.")
    if _is_reparse_point(content_dir.lstat()):
        raise PackagingError("Content directory cannot be a reparse point.")

    try:
        destination_input = Path(destination_folder)
        destination_lexical = Path(os.path.abspath(destination_input))
        destination = destination_input.resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise PackagingError(f"Cannot access destination directory: {exc}") from exc
    if not destination.is_dir():
        raise PackagingError("Destination must be an existing directory.")
    if not os.access(destination, os.W_OK):
        raise PackagingError("Destination is not writable.")
    build_root = content_dir.parent
    if _is_build_tree_destination(
        destination_lexical, build_root
    ) or _is_build_tree_destination(destination, build_root):
        raise PackagingError("Destination cannot be inside the build directory.")
    return content_dir, destination


def validate_package_destination(
    content_folder: str | os.PathLike,
    destination_folder: str | os.PathLike,
) -> str:
    """Validate a selected output directory without changing the filesystem."""
    _, destination = _validated_package_paths(content_folder, destination_folder)
    return str(destination)


def validate_package_spec(
    spec: PackageSpec,
    recognized_content_roots: Optional[Iterable[str]] = None,
) -> str:
    """Validate a package without creating, deleting, or changing any files."""
    return PackagingPipeline(spec).validate(recognized_content_roots)


class PackagingPipeline:
    def __init__(self, spec: PackageSpec):
        self.spec = spec
        self.log = get_logger(__name__)
        self.seven_zip_path = find_7z_executable()
        self._is_cancelled: Callable[[], bool] = lambda: False
        self._normalized_guid = ""
        self._cover_bytes: bytes | None = None
        if self.seven_zip_path:
            self.log.info("7-Zip executable found at: %s", self.seven_zip_path)
        else:
            self.log.info("7-Zip executable not found, using built-in zipfile module.")

    def execute(
        self,
        *,
        progress: Callable[[int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> PackageResult:
        progress_callback = progress or (lambda _percent, _stage: None)

        def report_progress(percent: int, stage: str) -> None:
            try:
                progress_callback(percent, stage)
            except Exception as exc:
                self.log.warning("Packaging progress callback failed: %s", exc)

        self._is_cancelled = is_cancelled or (lambda: False)
        final_path: Optional[Path] = None

        try:
            report_progress(2, "Validating")
            final_path = self._validate_spec()
            source_inventory = self._validate_source_inventory(
                self.spec.recognized_content_roots
            )
            self._check_cancelled()

            destination = final_path.parent
            with tempfile.TemporaryDirectory(
                prefix=".dimcreator-package-",
                dir=destination,
                ignore_cleanup_errors=True,
            ) as temp_dir:
                temp_root = Path(temp_dir)
                staging_root = temp_root / "staging"
                staging_content = staging_root / "Content"
                staging_content.mkdir(parents=True)

                report_progress(5, "Staging")
                self._copy_to_staging(source_inventory, staging_root, report_progress)

                report_progress(12, "Processing Image")
                self._process_image(staging_content)
                self._check_cancelled()

                content_inventory = PackageInventory.from_content(
                    staging_content,
                    cancel_check=self._is_cancelled,
                )
                report_progress(16, "Creating Manifest")
                self._create_manifest(staging_root, content_inventory)
                report_progress(19, "Creating Supplement")
                self._create_supplement(staging_root)
                inventory = content_inventory.with_entries(
                    self._metadata_inventory_entries(staging_root)
                )

                temp_zip = temp_root / f"{final_path.name}.tmp"

                def report_zip_progress(percent: int) -> None:
                    scaled_percent = 20 + int((percent / 100) * 75)
                    report_progress(scaled_percent, "Packaging")

                if self.seven_zip_path:
                    self._zip_with_7z(temp_zip, staging_root, inventory, report_zip_progress)
                else:
                    self._zip_with_zipfile(temp_zip, inventory, report_zip_progress)

                report_progress(96, "Verifying")
                self._verify_archive(temp_zip, final_path.name, inventory)
                self._check_cancelled()
                self._flush_file(temp_zip)
                file_size = temp_zip.stat().st_size
                self._publish_archive(temp_zip, final_path)

            report_progress(100, "Complete")
            self.log.info("DIM package created at: %s", final_path)
            return PackageResult(
                status=PackageStatus.SUCCESS,
                message="Packaging complete.",
                final_path=str(final_path),
                file_size=file_size,
            )
        except PackagingCancelled:
            self.log.info("Packaging cancelled.")
            return PackageResult(
                status=PackageStatus.CANCELLED,
                message="Cancelled by user",
                final_path=str(final_path) if final_path else None,
                cancelled=True,
            )
        except PackagingError as exc:
            self.log.error("Packaging failed: %s", exc)
            return PackageResult(
                status=PackageStatus.FAILED,
                message=str(exc),
                final_path=str(final_path) if final_path else None,
            )
        except Exception as exc:
            self.log.exception("Packaging failed: %s", exc)
            return PackageResult(
                status=PackageStatus.FAILED,
                message=str(exc),
                final_path=str(final_path) if final_path else None,
            )

    def validate(
        self,
        recognized_content_roots: Optional[Iterable[str]] = None,
    ) -> str:
        """Return the final output path after complete, side-effect-free validation."""
        final_path = self._validate_spec()
        roots = (
            recognized_content_roots
            if recognized_content_roots is not None
            else self.spec.recognized_content_roots
        )
        self._validate_source_inventory(roots)
        return str(final_path)

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise PackagingCancelled("Cancelled by user")

    def _publish_archive(self, temporary: Path, final_path: Path) -> None:
        if self.spec.replace_existing:
            os.replace(temporary, final_path)
            return
        try:
            if os.name == "nt":
                os.rename(temporary, final_path)
            else:
                os.link(temporary, final_path, follow_symlinks=False)
                temporary.unlink()
        except FileExistsError as exc:
            raise PackagingError(
                "Package output exists but was not approved for replacement."
            ) from exc

    def _validate_spec(self) -> Path:
        try:
            validate_dim_prefix(self.spec.prefix)
            validate_dim_sku(self.spec.sku)
            validate_dim_part(self.spec.product_part)
            zip_name = self._build_zip_name()
            validate_dim_zip_filename(zip_name)
        except ValueError as exc:
            raise PackagingError(str(exc)) from exc

        if not isinstance(self.spec.product_name, str) or not self.spec.product_name.strip():
            raise PackagingError("Product name is required.")
        if not isinstance(self.spec.store, str) or not self.spec.store.strip():
            raise PackagingError("Store is required.")
        if not isinstance(self.spec.product_tags, str):
            raise PackagingError("Product tags must be text.")
        if not isinstance(self.spec.clean_support, bool):
            raise PackagingError("Clean Support must be a boolean value.")
        if not isinstance(self.spec.replace_existing, bool):
            raise PackagingError("Replace Existing must be a boolean value.")
        content_only_tags = {
            tag.strip().casefold()
            for tag in self.spec.product_tags.split(",")
            if tag.strip()
        }
        if content_only_tags & {"plugin", "software"}:
            raise PackagingError("Plugin and Software packages are not supported yet.")
        try:
            self._normalized_guid = str(uuid.UUID(str(self.spec.guid)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise PackagingError("Package GUID is invalid.") from exc

        _, destination = _validated_package_paths(
            self.spec.content_dir,
            self.spec.destination_folder,
        )

        self._cover_bytes = None
        if self.spec.image_path:
            try:
                image_input = Path(self.spec.image_path)
                image_stat = image_input.lstat()
            except (OSError, TypeError) as exc:
                raise PackagingError(f"Cannot access cover image: {exc}") from exc
            if (
                not stat.S_ISREG(image_stat.st_mode)
                or image_input.is_symlink()
                or _is_reparse_point(image_stat)
            ):
                raise PackagingError(
                    "Cover image must be a regular file, not a link or reparse point."
                )
            if image_stat.st_size > MAX_COVER_BYTES:
                raise PackagingError("Cover image exceeds the 20 MiB limit.")
            try:
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                descriptor = os.open(image_input, flags)
                with os.fdopen(descriptor, "rb") as source:
                    opened_stat = os.fstat(source.fileno())
                    if (
                        not stat.S_ISREG(opened_stat.st_mode)
                        or opened_stat.st_size != image_stat.st_size
                    ):
                        raise PackagingError(
                            "Cover image changed while it was being validated."
                        )
                    cover_bytes = source.read(MAX_COVER_BYTES + 1)
                if len(cover_bytes) > MAX_COVER_BYTES:
                    raise PackagingError("Cover image exceeds the 20 MiB limit.")
                with Image.open(io.BytesIO(cover_bytes)) as image:
                    width, height = image.size
                    if (
                        width <= 0
                        or height <= 0
                        or width * height > MAX_COVER_PIXELS
                    ):
                        raise PackagingError(
                            "Cover image exceeds the 40 megapixel limit."
                        )
                    image.verify()
                self._cover_bytes = cover_bytes
            except PackagingError:
                raise
            except Exception as exc:
                raise PackagingError(f"Cover image is invalid: {exc}") from exc
            try:
                build_support_cover_filename(
                    self.spec.store,
                    self.spec.sku,
                    self.spec.product_name,
                )
            except ValueError as exc:
                raise PackagingError(str(exc)) from exc

        final_path = destination / zip_name
        if final_path.exists() and not final_path.is_file():
            raise PackagingError("Package output path is not a regular file.")
        return final_path

    def _validate_source_inventory(
        self,
        recognized_content_roots: Optional[Iterable[str]],
    ) -> PackageInventory:
        inventory = PackageInventory.from_content(
            self.spec.content_dir,
            clean_support=self.spec.clean_support,
            cancel_check=self._is_cancelled,
        )
        if not inventory.entries:
            raise PackagingError("Content folder has no packageable files.")
        if recognized_content_roots is None:
            return inventory

        roots: set[str] = set()
        for root in recognized_content_roots:
            if not isinstance(root, str):
                raise PackagingError("Recognized DAZ content roots must be text.")
            value = root.strip().replace("\\", "/").strip("/")
            if not value or "/" in value:
                raise PackagingError(f"Invalid DAZ content root: {root!r}")
            roots.add(value.casefold())
        if not roots:
            raise PackagingError("No recognized DAZ content roots are configured.")

        has_recognized_file = any(
            len(PurePosixPath(entry.archive_path).parts) >= 3
            and PurePosixPath(entry.archive_path).parts[1].casefold() in roots
            for entry in inventory.entries
        )
        invalid_root_files = [
            entry.archive_path
            for entry in inventory.entries
            if len(PurePosixPath(entry.archive_path).parts) == 2
            and PurePosixPath(entry.archive_path).parts[1].casefold() in roots
        ]
        if invalid_root_files:
            raise PackagingError(
                "Recognized DAZ roots must be directories, not files: "
                + ", ".join(invalid_root_files[:5])
            )
        if not has_recognized_file:
            raise PackagingError(
                "Content folder has no packageable file under a recognized DAZ root."
            )
        return inventory

    def _copy_to_staging(
        self,
        inventory: PackageInventory,
        staging_root: Path,
        progress_callback: Callable[[int, str], None],
    ) -> None:
        copied = 0
        total = max(1, inventory.total_size)
        for entry in inventory.entries:
            self._check_cancelled()
            target = staging_root.joinpath(*PurePosixPath(entry.archive_path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = Path(entry.source_path)
            try:
                source_stat = source.lstat()
            except OSError as exc:
                raise PackagingError(f"Cannot read package content {source}: {exc}") from exc
            if (
                not stat.S_ISREG(source_stat.st_mode)
                or source.is_symlink()
                or _is_reparse_point(source_stat)
            ):
                raise PackagingError(f"Package content changed while staging: {source}")
            try:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(source, flags)
                with os.fdopen(descriptor, "rb") as source_file, open(target, "wb") as target_file:
                    opened_stat = os.fstat(source_file.fileno())
                    if not stat.S_ISREG(opened_stat.st_mode):
                        raise PackagingError(f"Package content is not a regular file: {source}")
                    if (
                        opened_stat.st_size != entry.size
                        or (
                            source_stat.st_ino
                            and opened_stat.st_ino
                            and source_stat.st_ino != opened_stat.st_ino
                        )
                    ):
                        raise PackagingError(f"Package content changed while staging: {source}")
                    while True:
                        self._check_cancelled()
                        chunk = source_file.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        target_file.write(chunk)
                        copied += len(chunk)
                        progress_callback(5 + int((copied / total) * 5), "Staging")
            except OSError as exc:
                raise PackagingError(f"Cannot stage package content {source}: {exc}") from exc

    def _process_image(self, staging_content: Path) -> None:
        if not self.spec.image_path:
            return
        if self._cover_bytes is None:
            raise PackagingError("Cover image was not validated before packaging.")
        self._check_cancelled()
        image_name = build_support_cover_filename(
            self.spec.store,
            self.spec.sku,
            self.spec.product_name,
        )
        target_dir = staging_content / "Runtime" / "Support"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / image_name
        try:
            with Image.open(io.BytesIO(self._cover_bytes)) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.thumbnail((300, 300), Image.Resampling.LANCZOS)
                image.save(target_path, "JPEG")
        except Exception as exc:
            raise PackagingError(f"Image processing failed: {exc}") from exc

    def _create_manifest(
        self,
        staging_root: Path,
        inventory: PackageInventory,
    ) -> None:
        root = Element("DAZInstallManifest", VERSION="0.1")
        SubElement(root, "GlobalID", VALUE=self._normalized_guid)
        for member in inventory.manifest_members:
            SubElement(root, "File", TARGET="Content", ACTION="Install", VALUE=member)
        self._write_xml(staging_root / "Manifest.dsx", root)

    def _create_supplement(self, staging_root: Path) -> None:
        root = Element("ProductSupplement", VERSION="0.1")
        SubElement(root, "ProductName", VALUE=self.spec.product_name)
        store_idx = build_product_store_idx(
            self.spec.prefix,
            self.spec.sku,
            self.spec.product_part,
        )
        if store_idx is not None:
            SubElement(root, "ProductStoreIDX", VALUE=store_idx)
        SubElement(root, "InstallTypes", VALUE="Content")
        SubElement(root, "ProductTags", VALUE=self.spec.product_tags)
        self._write_xml(staging_root / "Supplement.dsx", root)

    @staticmethod
    def _write_xml(path: Path, root: Element) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as output:
            output.write(prettify(root))

    @staticmethod
    def _metadata_inventory_entries(
        staging_root: Path,
    ) -> tuple[PackageInventoryEntry, ...]:
        entries = []
        for name in ("Manifest.dsx", "Supplement.dsx"):
            path = staging_root / name
            path_stat = path.lstat()
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or path.is_symlink()
                or _is_reparse_point(path_stat)
            ):
                raise PackagingError(f"Generated package metadata is unsafe: {name}")
            entries.append(
                PackageInventoryEntry(
                    source_path=str(path),
                    archive_path=name,
                    size=path_stat.st_size,
                )
            )
        return tuple(entries)

    def _build_zip_name(self) -> str:
        return build_dim_zip_filename(
            self.spec.prefix,
            self.spec.sku,
            self.spec.product_part,
            self.spec.product_name,
        )

    def _zip_with_zipfile(
        self,
        zip_path: Path,
        inventory: PackageInventory,
        progress_callback: Callable[[int], None],
    ) -> None:
        written = 0
        total = max(1, inventory.total_size)
        with zipfile.ZipFile(
            zip_path,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=False,
        ) as archive:
            for entry in inventory.entries:
                self._check_cancelled()
                try:
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(entry.source_path, flags)
                    with os.fdopen(descriptor, "rb") as source, archive.open(
                        entry.archive_path,
                        mode="w",
                        force_zip64=entry.size >= zipfile.ZIP64_LIMIT,
                    ) as target:
                        while True:
                            self._check_cancelled()
                            chunk = source.read(COPY_CHUNK_SIZE)
                            if not chunk:
                                break
                            target.write(chunk)
                            written += len(chunk)
                            progress_callback(min(99, int((written / total) * 100)))
                except OSError as exc:
                    raise PackagingError(
                        f"Cannot add package member {entry.archive_path!r}: {exc}"
                    ) from exc
        progress_callback(100)

    def _zip_with_7z(
        self,
        zip_path: Path,
        staging_root: Path,
        inventory: PackageInventory,
        progress_callback: Callable[[int], None],
    ) -> None:
        list_path = zip_path.with_suffix(".files.txt")
        list_path.write_text("\n".join(inventory.archive_members), encoding="utf-8")
        command = [
            str(self.seven_zip_path),
            "a",
            "-tzip",
            "-mx=6",
            "-bsp1",
            "-bso1",
            "-bse1",
            "-y",
            "-scsUTF-8",
            "-sccUTF-8",
            str(zip_path),
            f"@{list_path}",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=staging_root,
            shell=False,
            **hidden_subprocess_kwargs(),
        )
        output_queue: queue.Queue[Optional[bytes]] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            try:
                while chunk := process.stdout.read1(4096):
                    output_queue.put(chunk)
            finally:
                output_queue.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        started = last_progress = time.monotonic()
        output_tail: deque[str] = deque(maxlen=40)
        reader_finished = False
        progress_buffer = ""
        last_percent = -1

        try:
            while process.poll() is None or not reader_finished:
                self._check_cancelled()
                now = time.monotonic()
                if now - started > SEVEN_ZIP_TOTAL_TIMEOUT_SECONDS:
                    raise PackagingError("7-Zip exceeded the four-hour packaging timeout.")
                if now - last_progress > SEVEN_ZIP_PROGRESS_TIMEOUT_SECONDS:
                    raise PackagingError("7-Zip made no progress for five minutes.")
                try:
                    chunk = output_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk is None:
                    reader_finished = True
                    continue
                last_progress = now
                text = chunk.decode("utf-8", errors="replace")
                output_tail.extend(part for part in re.split(r"[\r\n]+", text) if part)
                progress_buffer = (progress_buffer + text)[-512:]
                matches = list(re.finditer(r"(\d{1,3})\s*%", progress_buffer))
                if matches:
                    percent = max(0, min(100, int(matches[-1].group(1))))
                    if percent > last_percent:
                        progress_callback(percent)
                        last_percent = percent
                    progress_buffer = progress_buffer[matches[-1].end():]

            return_code = process.wait()
            if return_code != 0:
                details = "\n".join(output_tail).strip() or "No diagnostic output."
                raise PackagingError(f"7-Zip failed with code {return_code}: {details}")
            progress_callback(100)
        except BaseException:
            self._stop_process(process)
            raise
        finally:
            reader.join(timeout=1)
            if process.stdout:
                process.stdout.close()
            try:
                list_path.unlink()
            except OSError:
                pass

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _verify_archive(
        self,
        zip_path: Path,
        final_name: str,
        inventory: PackageInventory,
    ) -> None:
        validate_dim_zip_filename(final_name)
        expected = set(inventory.archive_members)
        expected_folded = {name.casefold() for name in expected}
        if len(expected_folded) != len(expected):
            raise PackagingError("Expected package members contain a name collision.")

        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                infos = archive.infolist()
                if any(info.is_dir() for info in infos):
                    raise PackagingError("ZIP contains unexpected directory entries.")
                actual = [info.filename for info in infos]
                if any("\\" in name for name in actual):
                    raise PackagingError("ZIP contains non-canonical member paths.")
                if len(actual) != len(set(actual)):
                    raise PackagingError("ZIP contains duplicate members.")
                if len({name.casefold() for name in actual}) != len(actual):
                    raise PackagingError("ZIP contains case-insensitive member collisions.")
                if set(actual) != expected:
                    missing = sorted(expected - set(actual))
                    unexpected = sorted(set(actual) - expected)
                    raise PackagingError(
                        f"ZIP member verification failed; missing={missing}, "
                        f"unexpected={unexpected}."
                    )

                expected_sizes = {
                    entry.archive_path: entry.size for entry in inventory.entries
                }
                for info in infos:
                    if info.file_size != expected_sizes[info.filename]:
                        raise PackagingError(
                            f"ZIP member size verification failed for {info.filename!r}."
                        )
                    self._check_cancelled()
                    with archive.open(info, "r") as member:
                        while member.read(COPY_CHUNK_SIZE):
                            self._check_cancelled()

                manifest_root = ElementTree.fromstring(archive.read("Manifest.dsx"))
                supplement_root = ElementTree.fromstring(archive.read("Supplement.dsx"))
        except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise PackagingError(f"ZIP verification failed: {exc}") from exc

        if manifest_root.tag != "DAZInstallManifest":
            raise PackagingError("Manifest.dsx has an invalid root element.")
        if manifest_root.attrib.get("VERSION") != "0.1":
            raise PackagingError("Manifest.dsx has an unsupported version.")
        global_ids = manifest_root.findall("GlobalID")
        if (
            len(global_ids) != 1
            or global_ids[0].attrib.get("VALUE") != self._normalized_guid
        ):
            raise PackagingError("Manifest.dsx has an invalid GlobalID.")
        manifest_files = manifest_root.findall("File")
        if any(
            element.attrib.get("TARGET") != "Content"
            or element.attrib.get("ACTION") != "Install"
            for element in manifest_files
        ):
            raise PackagingError("Manifest.dsx contains an invalid install action.")
        manifest_members = {
            element.attrib.get("VALUE", "")
            for element in manifest_files
        }
        if manifest_members != set(inventory.manifest_members):
            raise PackagingError("Manifest and Content archive members do not match.")
        if len(manifest_files) != len(manifest_members):
            raise PackagingError("Manifest.dsx contains duplicate file entries.")
        if supplement_root.tag != "ProductSupplement":
            raise PackagingError("Supplement.dsx has an invalid root element.")
        if supplement_root.attrib.get("VERSION") != "0.1":
            raise PackagingError("Supplement.dsx has an unsupported version.")
        expected_supplement = {
            "ProductName": self.spec.product_name,
            "InstallTypes": "Content",
            "ProductTags": self.spec.product_tags,
        }
        store_idx = build_product_store_idx(
            self.spec.prefix,
            self.spec.sku,
            self.spec.product_part,
        )
        if store_idx is not None:
            expected_supplement["ProductStoreIDX"] = store_idx
        elif supplement_root.findall("ProductStoreIDX"):
            raise PackagingError(
                "Supplement.dsx must let DIM derive custom source identifiers."
            )
        for tag, expected_value in expected_supplement.items():
            elements = supplement_root.findall(tag)
            if len(elements) != 1 or elements[0].attrib.get("VALUE") != expected_value:
                raise PackagingError(f"Supplement.dsx has an invalid {tag} value.")

    @staticmethod
    def _flush_file(path: Path) -> None:
        with open(path, "r+b") as output:
            output.flush()
            os.fsync(output.fileno())


class BatchPackagingWorker(QThread):
    overallProgress = Signal(int, int)
    buildStarted = Signal(int, str, str)
    buildProgress = Signal(int, str)
    buildCompleted = Signal(int, bool, str, int, str)
    allCompleted = Signal(dict)

    def __init__(self, build_specs, parent=None):
        super().__init__(parent)
        self.build_specs = build_specs
        self._cancel_event = threading.Event()
        self.log = get_logger(__name__)

    def requestCancellation(self) -> None:
        self._cancel_event.set()
        self.log.info("Cancellation requested for batch packaging")

    def _duplicate_output_indices(self) -> set[int]:
        output_paths: dict[str, list[int]] = {}
        for index, (_, spec) in enumerate(self.build_specs):
            try:
                name = build_dim_zip_filename(
                    spec.prefix,
                    spec.sku,
                    spec.product_part,
                    spec.product_name,
                )
                destination = Path(spec.destination_folder).resolve(strict=False)
                key = str(destination / name).casefold()
            except (OSError, ValueError):
                continue
            output_paths.setdefault(key, []).append(index)
        return {
            index
            for indices in output_paths.values()
            if len(indices) > 1
            for index in indices
        }

    def run(self) -> None:
        results = []
        total_builds = len(self.build_specs)
        duplicate_indices = self._duplicate_output_indices()

        for index, (build, spec) in enumerate(self.build_specs):
            if self._cancel_event.is_set():
                for remaining_build, _ in self.build_specs[index:]:
                    results.append(
                        {
                            "build": remaining_build,
                            "success": False,
                            "message": "Cancelled by user",
                            "file_size": 0,
                            "output_path": None,
                            "skipped": True,
                        }
                    )
                break

            part_label = f"Build {build.part:02d}"
            product_name = spec.product_name or "(No name)"
            self.buildStarted.emit(index, part_label, product_name)

            if index in duplicate_indices:
                result = PackageResult(
                    status=PackageStatus.FAILED,
                    message="Duplicate batch output path.",
                )
            else:
                pipeline = PackagingPipeline(spec)
                result = pipeline.execute(
                    progress=lambda percent, stage: self.buildProgress.emit(
                        percent,
                        stage,
                    ),
                    is_cancelled=self._cancel_event.is_set,
                )

            self.buildCompleted.emit(
                index,
                result.success,
                result.message,
                result.file_size,
                result.final_path or "",
            )
            results.append(
                {
                    "build": build,
                    "success": result.success,
                    "message": result.message,
                    "file_size": result.file_size,
                    "output_path": result.final_path,
                    "skipped": result.cancelled,
                }
            )
            self.overallProgress.emit(index + 1, total_builds)
            if result.cancelled:
                self.requestCancellation()

        summary = {
            "results": results,
            "total": total_builds,
            "successful": sum(
                1 for result in results if result["success"] and not result.get("skipped", False)
            ),
            "failed": sum(
                1
                for result in results
                if not result["success"] and not result.get("skipped", False)
            ),
            "skipped": sum(1 for result in results if result.get("skipped", False)),
        }
        self.allCompleted.emit(summary)
