import os
import shutil
import tempfile
import patoolib
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QThread, Signal

from logger_utils import get_logger
from utils import suppress_cmd_window, downloads_dir, get_optimal_workers

log = get_logger(__name__)


class ContentExtractionWorker(QThread):
    """
    A worker thread for extracting archive files and organizing content.
    """
    extractionComplete = Signal()
    extractionError = Signal(str)

    def __init__(
        self, archive_file_path, daz_folders, content_dir,
        copy_template_files, template_destination, parent=None
    ):
        super().__init__(parent)
        self.archive_file_path = archive_file_path
        self.daz_folders = {s.casefold() for s in daz_folders}
        self.content_dir = content_dir
        self.copy_template_files = copy_template_files
        self.template_destination = template_destination or downloads_dir()
        self.copiedTemplates = []

    def run(self):
        """
        Main execution method for the extraction process.
        """
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
        """
        Copies template archives to the specified destination.
        """
        if self.copy_template_files:
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
        else:
            log.info("Not copying template file as per user setting.")
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
        """
        Scans a directory to find DAZ-related content and embedded archives.
        """
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
        """
        Extracts and processes an archive found within the main archive.
        """
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
        """
        Extracts DAZ-specific content from a directory to the content_dir.
        """
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