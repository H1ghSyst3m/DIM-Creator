import os
import re
import shutil
import tempfile
import patoolib
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QThread, Signal

from logger_utils import get_logger
from utils import suppress_cmd_window, downloads_dir, get_optimal_workers

log = get_logger(__name__)


def classify_archives(archive_files, enable_template_detection):
    content_archives = []
    template_archives = []
    ignored_archives = []
    
    for archive_path in archive_files:
        basename = os.path.basename(archive_path).lower()
        
        if "templ" in basename:
            if enable_template_detection:
                template_archives.append(archive_path)
            else:
                ignored_archives.append(archive_path)
        else:
            content_archives.append(archive_path)
    
    return content_archives, template_archives, ignored_archives


def detect_heuristic_ordering(archive_files):
    """Detect part numbers from filenames (_XofY, _PartXX, build number, trailing number, alphabetical)."""
    if not archive_files:
        return [], None
    
    if len(archive_files) == 1:
        return archive_files, None
    
    # Heuristic 1: _XofY pattern
    pattern1 = re.compile(r'_(\d+)of\d+', re.IGNORECASE)
    matches = []
    for f in archive_files:
        basename = os.path.basename(f)
        match = pattern1.search(basename)
        if match:
            part_num = int(match.group(1))
            matches.append((part_num, f))
    
    if len(matches) == len(archive_files):
        matches.sort(key=lambda x: x[0])
        return [f for _, f in matches], None
    
    # Heuristic 2: _PartXX pattern
    pattern2 = re.compile(r'_part\s*(\d+)', re.IGNORECASE)
    matches = []
    for f in archive_files:
        basename = os.path.basename(f)
        match = pattern2.search(basename)
        if match:
            part_num = int(match.group(1))
            matches.append((part_num, f))
    
    if len(matches) == len(archive_files):
        matches.sort(key=lambda x: x[0])
        return [f for _, f in matches], None
    
    # Heuristic 3: Build number before ID (e.g. Product_2_281358.zip), lookbehind avoids partial matches
    pattern3 = re.compile(r'(?<!\d)(\d{1,2})_\d{5,}\.(?:zip|rar|7z)$', re.IGNORECASE)
    matches = []
    for f in archive_files:
        basename = os.path.basename(f)
        match = pattern3.search(basename)
        if match:
            part_num = int(match.group(1))
            matches.append((part_num, f))
    
    if len(matches) == len(archive_files):
        part_numbers = [m[0] for m in matches]
        if len(part_numbers) != len(set(part_numbers)):
            log.warning("Duplicate part numbers detected in heuristic 3, trying next pattern before falling back to alphabetical sort")
        else:
            matches.sort(key=lambda x: x[0])
            return [f for _, f in matches], None
    
    # Heuristic 4: Trailing number before extension (e.g. Product_01.zip), lookbehind avoids partial matches
    pattern4 = re.compile(r'(?<!\d)_(\d{1,2})\.(?:zip|rar|7z)$', re.IGNORECASE)
    matches = []
    for f in archive_files:
        basename = os.path.basename(f)
        match = pattern4.search(basename)
        if match:
            part_num = int(match.group(1))
            matches.append((part_num, f))
    
    if len(matches) == len(archive_files):
        part_numbers = [m[0] for m in matches]
        if len(part_numbers) != len(set(part_numbers)):
            log.warning("Duplicate part numbers detected in heuristic 4, falling back to alphabetical sort")
        else:
            matches.sort(key=lambda x: x[0])
            return [f for _, f in matches], None
    
    # Heuristic 5: Fallback to alphabetical
    sorted_files = sorted(archive_files, key=lambda f: os.path.basename(f).lower())
    warning = "Could not detect build numbering pattern. Archives ordered alphabetically."
    return sorted_files, warning


class ContentExtractionWorker(QThread):
    extractionComplete = Signal()
    extractionError = Signal(str)

    def __init__(
        self, archive_file_path, daz_folders, content_dir,
        enable_template_detection, template_destination, parent=None
    ):
        super().__init__(parent)
        self.archive_file_path = archive_file_path
        self.daz_folders = {s.casefold() for s in daz_folders}
        self.content_dir = content_dir
        self.enable_template_detection = enable_template_detection
        self.template_destination = template_destination or downloads_dir()
        self.copiedTemplates = []

    def run(self):
        with suppress_cmd_window():
            log.info(f"Starting extraction of {self.archive_file_path}")
            success = False
            temp_dir = None
            try:
                temp_dir = tempfile.mkdtemp()
                patoolib.extract_archive(self.archive_file_path, outdir=temp_dir)
                log.info(f"Archive extracted to temporary directory: [{temp_dir}]")

                base_paths, embedded_archive_files = self.scanDirectory(temp_dir)
                template_archives = [
                    f for f in embedded_archive_files if "templ" in
                    os.path.basename(f).lower()
                ]
                remaining_archives = [
                    f for f in embedded_archive_files
                    if f not in template_archives
                ]

                if len(remaining_archives) > 1:
                    self.extractionError.emit(
                        "Multiple archive files found, canceling extraction."
                    )
                    return
                elif len(remaining_archives) == 1:
                    if template_archives:
                        self.copyTemplateArchive(template_archives[0])
                    self.processEmbeddedArchive(remaining_archives[0], base_paths)
                    success = True
                elif base_paths:
                    if template_archives:
                        self.copyTemplateArchive(template_archives[0])
                    self.extractRelevantContent(temp_dir, base_paths)
                    success = True
                else:
                    self.extractionError.emit(
                        "No recognized daz main folders found in the archive."
                    )
                    return

            except Exception as e:
                msg = str(e)
                if "7z" in msg.lower() or "unrar" in msg.lower():
                    self.extractionError.emit(
                        "No suitable extractor found (7-Zip or UnRAR). "
                        "Please install and try again."
                    )
                else:
                    self.extractionError.emit(msg)
            finally:
                try:
                    if temp_dir and os.path.isdir(temp_dir):
                        shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass
                if success:
                    self.extractionComplete.emit()

    def copyTemplateArchive(self, template_archive_path: str):
        if not os.path.exists(self.template_destination):
            os.makedirs(self.template_destination, exist_ok=True)
        target_path = os.path.join(
            self.template_destination,
            os.path.basename(template_archive_path)
        )
        shutil.copy2(template_archive_path, target_path)
        template_file = os.path.basename(template_archive_path)
        self.copiedTemplates.append(template_file)
        log.info(
            f"Copied template archive [{template_archive_path}] to "
            f"[{self.template_destination}]"
        )
        try:
            os.remove(template_archive_path)
            log.info(
                "Removed template archive from temporary directory: "
                f"[{template_archive_path}]"
            )
        except Exception as e:
            log.error(
                "Failed to remove template archive from temporary directory: "
                f"[{e}]"
            )

    def scanDirectory(self, directory: str):
        base_paths = set()
        embedded_archive_files = []

        for root, _, files in os.walk(directory):
            for fname in files:
                fpath = os.path.join(root, fname)
                lower = fname.casefold()

                if lower.endswith(('.zip', '.rar', '.7z')):
                    embedded_archive_files.append(fpath)
                    continue

                rel = os.path.relpath(fpath, start=directory)
                parts = rel.split(os.sep)
                for i, segment in enumerate(parts):
                    if segment.casefold() in self.daz_folders:
                        base_paths.add(os.sep.join(parts[:i]))
                        break

        return base_paths, embedded_archive_files

    def processEmbeddedArchive(
        self, embedded_archive_path: str, base_paths: set
    ):
        with tempfile.TemporaryDirectory() as nested_temp_dir:
            try:
                patoolib.extract_archive(
                    embedded_archive_path, outdir=nested_temp_dir
                )
                new_base_paths, _ = self.scanDirectory(nested_temp_dir)

                if new_base_paths:
                    self.extractRelevantContent(nested_temp_dir, new_base_paths)
                else:
                    self.extractionError.emit(
                        "No recognized DAZ main folders found in the "
                        "embedded archive."
                    )
                    return

            except Exception as e:
                msg = str(e)
                if "7z" in msg.lower() or "unrar" in msg.lower():
                    self.extractionError.emit(
                        "No suitable extractor found (7-Zip or UnRAR). "
                        "Please install and try again."
                    )
                else:
                    self.extractionError.emit(msg)
                return
            finally:
                log.info(
                    "Cleaning up temporary files from embedded archive "
                    "extraction."
                )

    def extractRelevantContent(self, directory: str, base_paths: set):
        try:
            if base_paths:
                base_abs_candidates = [
                    os.path.normpath(os.path.join(directory, bp))
                    for bp in base_paths
                ]
                common_base = os.path.commonpath(base_abs_candidates)
            else:
                common_base = os.path.normpath(directory)

            directory_abs = os.path.abspath(directory)
            common_base = os.path.abspath(common_base)
            if os.path.commonpath([directory_abs, common_base]) != directory_abs:
                common_base = directory_abs

            def _safe_join(base, rel):
                rel_norm = os.path.normpath(rel)
                dst = os.path.abspath(os.path.join(base, rel_norm))
                base_abs = os.path.abspath(base)
                if os.path.commonpath([dst, base_abs]) != base_abs:
                    raise ValueError(f"Unsafe path outside content dir: {rel}")
                return dst

            log.info(
                f"Starting to extract relevant content from [{directory_abs}] "
                f"with base path [{common_base}]"
            )

            for root, dirs, _ in os.walk(directory_abs):
                if os.path.commonpath(
                    [os.path.abspath(root), common_base]
                ) != common_base:
                    continue
                for d in dirs:
                    src_dir = os.path.join(root, d)
                    rel_dir = os.path.relpath(src_dir, common_base)
                    try:
                        dst_dir = _safe_join(self.content_dir, rel_dir)
                        os.makedirs(dst_dir, exist_ok=True)
                    except ValueError as ve:
                        log.error(str(ve))
                        self.extractionError.emit(str(ve))
                        return
                    except Exception as e:
                        log.error(f"Failed to create directory [{rel_dir}]: {e}")

            ignore_names = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}
            files_to_copy = []
            for root, _, files in os.walk(directory_abs):
                if os.path.commonpath(
                    [os.path.abspath(root), common_base]
                ) != common_base:
                    continue
                for fname in files:
                    if fname in ignore_names:
                        continue
                    src = os.path.join(root, fname)
                    if os.path.islink(src):
                        log.warning(f"Skipping symlink: {src}")
                        continue
                    rel = os.path.relpath(src, common_base)
                    try:
                        dst = _safe_join(self.content_dir, rel)
                        files_to_copy.append((src, dst))
                    except ValueError as ve:
                        log.error(str(ve))
                        self.extractionError.emit(str(ve))
                        return

            def copy_file(pair):
                src, dst = pair
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    log.info(f"Copied file [{src}] to [{dst}]")
                except Exception as e:
                    log.error(f"Failed to copy file [{src}] to [{dst}]: {e}")

            if files_to_copy:
                with ThreadPoolExecutor(
                    max_workers=get_optimal_workers()
                ) as executor:
                    list(executor.map(copy_file, files_to_copy))

            log.info("Completed extracting relevant content.")

        except Exception as e:
            log.error(f"Extraction failed: {e}")
            try:
                self.extractionError.emit(str(e))
            except Exception:
                pass


class MultiBuildExtractionWorker(QThread):
    extractionComplete = Signal(list)
    extractionError = Signal(str)
    extractionProgress = Signal(str)
    
    def __init__(
        self, content_archives, template_archives, daz_folders, 
        session, enable_template_detection, template_destination, parent=None
    ):
        super().__init__(parent)
        self.content_archives = content_archives
        self.template_archives = template_archives
        self.daz_folders = {s.casefold() for s in daz_folders}
        self.session = session
        self.enable_template_detection = enable_template_detection
        self.template_destination = template_destination or downloads_dir()
        self.copiedTemplates = []
        self.modified_builds = []
        
        self.part_to_build = {b.part: b for b in self.session.builds}
    
    def run(self):
        with suppress_cmd_window():
            try:
                for part_num, archive_path in enumerate(self.content_archives, 1):
                    log.info(f"Processing Build {part_num}: {archive_path}")
                    self.extractionProgress.emit(
                        f"Extracting Build {part_num}/{len(self.content_archives)}..."
                    )
                    
                    success = self._extractArchiveToBuild(archive_path, part_num)
                    if not success:
                        return
                
                for template_path in self.template_archives:
                    log.info(f"Copying template: {template_path}")
                    self.extractionProgress.emit(
                        f"Copying template: {os.path.basename(template_path)}..."
                    )
                    self._copyTemplateArchive(template_path)
                
                self.extractionComplete.emit(self.modified_builds)
                
            except Exception as e:
                msg = str(e)
                if "7z" in msg.lower() or "unrar" in msg.lower():
                    self.extractionError.emit(
                        "No suitable extractor found (7-Zip or UnRAR). "
                        "Please install and try again."
                    )
                else:
                    user_msg = (
                        f"{msg}\n\n"
                        "Warning: Extraction may have partially completed before this error. "
                        "Some builds created or modified during this operation may now appear "
                        "in the current session but will not be saved to disk. "
                        "You may want to review and manually clean up any incomplete builds."
                    )
                    self.extractionError.emit(user_msg)
    
    def _extractArchiveToBuild(self, archive_path, part_num):
        from utils import get_build_content_dir, create_build_folder
        from build_manager import create_build
        
        temp_dir = None
        build = None
        try:
            existing_build = self._findBuildByBuildNumber(part_num)
            
            if existing_build:
                log.info(f"Appending to existing build: {existing_build.folder}")
                build = existing_build
                build_folder = build.folder
                content_dir = get_build_content_dir(build_folder)
            else:
                log.info(f"Creating new build for Build {part_num}")
                build = create_build(self.session)
                build.part = part_num
                build_folder = build.folder
                content_dir = get_build_content_dir(build_folder)
                log.info(f"Created new build: {build.folder}")
                self.part_to_build[part_num] = build
            
            if build_folder not in self.modified_builds:
                self.modified_builds.append(build_folder)
            
            temp_dir = tempfile.mkdtemp()
            patoolib.extract_archive(archive_path, outdir=temp_dir)
            log.info(f"Archive extracted to temporary directory: [{temp_dir}]")
            
            base_paths, embedded_archive_files = self._scanDirectory(temp_dir)
            
            embedded_archive_files = [
                f for f in embedded_archive_files 
                if "templ" not in os.path.basename(f).lower()
            ]
            
            if len(embedded_archive_files) > 1:
                self.extractionError.emit(
                    f"Multiple archive files found in {os.path.basename(archive_path)}, "
                    "canceling extraction."
                )
                return False
            elif len(embedded_archive_files) == 1:
                self._processEmbeddedArchive(
                    embedded_archive_files[0], content_dir
                )
            elif base_paths:
                self._extractRelevantContent(temp_dir, base_paths, content_dir)
            else:
                self.extractionError.emit(
                    f"No recognized DAZ main folders found in "
                    f"{os.path.basename(archive_path)}."
                )
                return False
            
            from build_manager import validate_build, get_build_data
            if build:
                effective_data = get_build_data(self.session, build)
                build.content_status = validate_build(
                    build,
                    content_dir,
                    list(self.daz_folders),
                    effective_values=effective_data
                )
            
            return True
            
        except Exception as e:
            log.error(f"Failed to extract {archive_path}: {e}")
            self.extractionError.emit(f"Failed to extract {os.path.basename(archive_path)}: {str(e)}")
            return False
        finally:
            if temp_dir and os.path.isdir(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as e:
                    log.error(f"Failed to cleanup temp directory: {e}")
    
    def _findBuildByBuildNumber(self, part_num):
        return self.part_to_build.get(part_num)
    
    def _copyTemplateArchive(self, template_path):
        if not os.path.exists(self.template_destination):
            os.makedirs(self.template_destination, exist_ok=True)
        
        target_path = os.path.join(
            self.template_destination,
            os.path.basename(template_path)
        )
        
        try:
            shutil.copy2(template_path, target_path)
            template_file = os.path.basename(template_path)
            self.copiedTemplates.append(template_file)
            log.info(f"Copied template archive to [{self.template_destination}]")
        except Exception as e:
            log.error(f"Failed to copy template: {e}")
            raise
    
    def _scanDirectory(self, directory):
        base_paths = set()
        embedded_archive_files = []
        
        for root, _, files in os.walk(directory):
            for fname in files:
                fpath = os.path.join(root, fname)
                lower = fname.casefold()
                
                if lower.endswith(('.zip', '.rar', '.7z')):
                    embedded_archive_files.append(fpath)
                    continue
                
                rel = os.path.relpath(fpath, start=directory)
                parts = rel.split(os.sep)
                for i, segment in enumerate(parts):
                    if segment.casefold() in self.daz_folders:
                        base_paths.add(os.sep.join(parts[:i]))
                        break
        
        return base_paths, embedded_archive_files
    
    def _processEmbeddedArchive(self, embedded_archive_path, content_dir):
        with tempfile.TemporaryDirectory() as nested_temp_dir:
            try:
                patoolib.extract_archive(
                    embedded_archive_path, outdir=nested_temp_dir
                )
                new_base_paths, _ = self._scanDirectory(nested_temp_dir)
                
                if new_base_paths:
                    self._extractRelevantContent(
                        nested_temp_dir, new_base_paths, content_dir
                    )
                else:
                    self.extractionError.emit(
                        "No recognized DAZ main folders found in embedded archive."
                    )
                    raise ValueError("No DAZ content in embedded archive")
                    
            except Exception as e:
                log.error(f"Failed to process embedded archive: {e}")
                raise
    
    def _extractRelevantContent(self, directory, base_paths, content_dir):
        try:
            if base_paths:
                base_abs_candidates = [
                    os.path.normpath(os.path.join(directory, bp))
                    for bp in base_paths
                ]
                common_base = os.path.commonpath(base_abs_candidates)
            else:
                common_base = os.path.normpath(directory)
            
            directory_abs = os.path.abspath(directory)
            common_base = os.path.abspath(common_base)
            if os.path.commonpath([directory_abs, common_base]) != directory_abs:
                common_base = directory_abs
            
            def _safe_join(base, rel):
                rel_norm = os.path.normpath(rel)
                dst = os.path.abspath(os.path.join(base, rel_norm))
                base_abs = os.path.abspath(base)
                if os.path.commonpath([dst, base_abs]) != base_abs:
                    raise ValueError(f"Unsafe path outside content dir: {rel}")
                return dst
            
            log.info(f"Extracting content to [{content_dir}]")
            
            for root, dirs, _ in os.walk(directory_abs):
                if os.path.commonpath(
                    [os.path.abspath(root), common_base]
                ) != common_base:
                    continue
                for d in dirs:
                    src_dir = os.path.join(root, d)
                    rel_dir = os.path.relpath(src_dir, common_base)
                    try:
                        dst_dir = _safe_join(content_dir, rel_dir)
                        os.makedirs(dst_dir, exist_ok=True)
                    except ValueError as ve:
                        log.error(str(ve))
                        raise
            
            ignore_names = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}
            files_to_copy = []
            for root, _, files in os.walk(directory_abs):
                if os.path.commonpath(
                    [os.path.abspath(root), common_base]
                ) != common_base:
                    continue
                for fname in files:
                    if fname in ignore_names:
                        continue
                    src = os.path.join(root, fname)
                    if os.path.islink(src):
                        log.warning(f"Skipping symlink: {src}")
                        continue
                    rel = os.path.relpath(src, common_base)
                    try:
                        dst = _safe_join(content_dir, rel)
                        files_to_copy.append((src, dst))
                    except ValueError as ve:
                        log.error(str(ve))
                        raise
            
            def copy_file(pair):
                src, dst = pair
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    log.debug(f"Copied file [{src}] to [{dst}]")
                except Exception as e:
                    log.error(f"Failed to copy file [{src}] to [{dst}]: {e}")
            
            if files_to_copy:
                with ThreadPoolExecutor(
                    max_workers=get_optimal_workers()
                ) as executor:
                    list(executor.map(copy_file, files_to_copy))
            
            log.info("Completed extracting relevant content.")
            
        except Exception as e:
            log.error(f"Extraction failed: {e}")
            raise
