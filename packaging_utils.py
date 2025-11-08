import os
import re
import shutil
import stat
import zipfile
import subprocess
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
from PIL import Image, ImageOps
from PySide6.QtCore import QThread, Signal
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Any

from logger_utils import get_logger
from utils import calculate_total_size, find_7z_executable, suppress_cmd_window

log = get_logger(__name__)


def prettify(elem):
    """Return a pretty-printed XML string for the Element."""
    rough_string = tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    pretty = reparsed.toprettyxml(indent="  ")
    return '\n'.join(pretty.split('\n')[1:])


@dataclass
class PackageSpec:
    """A data class to hold all parameters for a packaging job."""
    content_dir: str
    store: str
    product_name: str
    prefix: str
    sku: str
    product_part: int
    product_tags: str
    image_path: Optional[str]
    clean_support: bool
    guid: str
    destination_folder: str


class PackagingPipeline:
    """Orchestrates the packaging process without UI/thread dependencies."""

    def __init__(self, spec: PackageSpec):
        self.spec = spec
        self.log = get_logger(__name__)
        self.seven_zip_path = find_7z_executable()
        if self.seven_zip_path:
            log.info(f"7-Zip executable found at: {self.seven_zip_path}")
        else:
            log.info("7-Zip executable not found, using built-in zipfile module.")

    def execute(self, callbacks: Dict[str, Callable[..., Any]]) -> tuple[bool, str]:
        """
        Executes the entire packaging pipeline.
        
        Args:
            callbacks: A dictionary of callback functions, e.g., 
                       {'progress': func(percent, message), 'error': func(message)}
        
        Returns:
            A tuple (success, message).
        """
        progress = callbacks.get('progress', lambda *args: None)

        try:
            # Step 1: Clean support directory if requested
            if self.spec.clean_support:
                progress(5, "Cleaning")
                if not self._clean_support_directory():
                    return False, "Failed to clean the Support directory."

            # Step 2: Process and paste image
            progress(10, "Processing Image")
            if not self._process_and_paste_image():
                return False, "Image processing failed."

            # Step 3: Create manifest
            progress(15, "Creating Manifest")
            if not self._create_manifest():
                return False, "Manifest creation failed."

            # Step 4: Create supplement
            progress(20, "Creating Supplement")
            if not self._create_supplement():
                return False, "Supplement creation failed."

            # Step 5: Zip everything
            def report_zip_progress(percent):
                # Scale zipping progress from 25% to 100%
                scaled_percent = 25 + int((percent / 100) * 75)
                progress(scaled_percent, "Packaging")

            if not self._zip_package(report_zip_progress):
                return False, "Failed to create ZIP archive."
            
            return True, "Packaging complete."

        except Exception as e:
            self.log.exception("An unexpected error occurred during packaging.")
            return False, str(e)

    def _clean_support_directory(self) -> bool:
        """Recursively delete the contents of the 'Runtime/Support' directory."""
        target_dir = os.path.join(self.spec.content_dir, "Runtime", "Support")
        if not os.path.exists(target_dir):
            return True

        self.log.info("Attempting to clean Support Directory: %s", target_dir)

        def handle_remove_readonly(os_error: OSError):
            """Clear the readonly bit and re-attempt the removal."""
            try:
                path = os_error.filename
                if not path:
                    return
                try:
                    os.chmod(path, stat.S_IWRITE)
                except Exception:
                    pass
                try:
                    if os.path.isdir(path) and not os.path.islink(path):
                        os.rmdir(path)
                    else:
                        os.remove(path)
                except Exception:
                    pass
            except Exception as e:
                self.log.error(f"Still failed to delete due to error object handling: {e}")

        for name in os.listdir(target_dir):
            p = os.path.join(target_dir, name)
            try:
                if os.path.isfile(p) or os.path.islink(p):
                    os.chmod(p, stat.S_IWRITE)
                    os.unlink(p)
                elif os.path.isdir(p):
                    shutil.rmtree(p, onexc=handle_remove_readonly)
            except Exception as e:
                self.log.error(f"Failed to delete {p}. Reason: {e}")
                return False

        self.log.info("Support directory successfully cleaned.")
        return True

    def _process_and_paste_image(self) -> bool:
        """Process and save the product image to the 'Runtime/Support' directory."""
        if not self.spec.image_path:
            self.log.info("No image path provided, skipping image processing.")
            return True

        self.log.info("Attempting to generate Product cover from: %s", self.spec.image_path)
        try:
            sanitized_product_name = re.sub(r'[^A-Za-z0-9._-]+', '_', self.spec.product_name).strip('_')
            store_formatted = re.sub(r'[^A-Za-z0-9._-]+', '_', self.spec.store).strip('_')
            new_image_name = f"{store_formatted}_{self.spec.sku}_{sanitized_product_name}.jpg"

            target_dir = os.path.join(self.spec.content_dir, "Runtime", "Support")
            os.makedirs(target_dir, exist_ok=True)
            new_image_path = os.path.join(target_dir, new_image_name)

            with Image.open(self.spec.image_path) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode != 'RGB':
                    img = img.convert("RGB")
                img.thumbnail((300, 300), Image.Resampling.LANCZOS)
                img.save(new_image_path, "JPEG")
                self.log.info("Product cover successfully generated at: %s", new_image_path)
            return True
        except Exception as e:
            self.log.error(f"An error occurred while processing the image: {e}")
            return False

    def _create_manifest(self) -> bool:
        """Create the Manifest.dsx file."""
        self.log.info("Attempting to generate Product Manifest.")
        try:
            root = Element('DAZInstallManifest', VERSION="0.1")
            SubElement(root, 'GlobalID', VALUE=self.spec.guid)

            for subdir, dirs, files in os.walk(self.spec.content_dir):
                dirs.sort()
                files.sort()
                for file in files:
                    file_path = os.path.join(subdir, file).replace("\\", "/")
                    rel_path = os.path.relpath(file_path, start=self.spec.content_dir).replace("\\", "/")
                    SubElement(root, 'File', TARGET="Content", ACTION="Install", VALUE=f"Content/{rel_path}")

            xml_str = prettify(root)
            manifest_path = os.path.join(os.path.dirname(self.spec.content_dir), "Manifest.dsx")
            with open(manifest_path, "w", encoding="utf-8", newline="\n") as mf:
                mf.write(xml_str)
            self.log.info("Product Manifest successfully generated at: %s", manifest_path)
            return True
        except Exception as e:
            self.log.error(f"An error occurred while creating the manifest: {e}")
            return False

    def _create_supplement(self) -> bool:
        """Create the Supplement.dsx file."""
        self.log.info("Attempting to generate Product Supplement.")
        try:
            root = Element('ProductSupplement', VERSION="0.1")
            SubElement(root, 'ProductName', VALUE=self.spec.product_name)
            SubElement(root, 'InstallTypes', VALUE="Content")
            SubElement(root, 'ProductTags', VALUE=self.spec.product_tags)

            xml_str = prettify(root)
            supplement_path = os.path.join(os.path.dirname(self.spec.content_dir), "Supplement.dsx")
            with open(supplement_path, "w", encoding="utf-8", newline="\n") as supplement_file:
                supplement_file.write(xml_str)
            self.log.info("Product Supplement successfully generated at: %s", supplement_path)
            return True
        except Exception as e:
            self.log.error(f"An error occurred while creating the supplement: {e}")
            return False

    def _build_zip_name(self) -> str:
        prefix_clean = re.sub(r'[^A-Za-z0-9]+', '', str(self.spec.prefix)).upper()
        try:
            sku_formatted = f"{int(str(self.spec.sku)):08d}"
        except ValueError:
            sku_formatted = str(self.spec.sku).zfill(8)

        part_str = f"{int(self.spec.product_part):02d}"
        sanitized_name = re.sub(r'[^A-Za-z0-9._-]+', '_', str(self.spec.product_name)).strip('_')
        return f"{prefix_clean}{sku_formatted}-{part_str}_{sanitized_name}.zip"

    def _zip_package(self, progress_callback: Callable[[int], None]) -> bool:
        """Create the final ZIP package."""
        zip_name = self._build_zip_name()
        zip_path = os.path.join(self.spec.destination_folder, zip_name)
        arc_base = os.path.dirname(self.spec.content_dir)

        if self.seven_zip_path:
            return self._zip_with_7z(zip_path, arc_base, progress_callback)
        else:
            return self._zip_with_zipfile(zip_path, arc_base, progress_callback)

    def _zip_with_7z(self, zip_path: str, arc_base: str, progress_callback: Callable[[int], None]) -> bool:
        self.log.info(f"Attempting to generate DIM file using 7-Zip: {zip_path}")
        
        command = [
            self.seven_zip_path,
            'a',
            '-tzip',
            '-mx=6',
            '-bsp1',
            zip_path,
            '.\\*'
        ]

        with suppress_cmd_window():
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=arc_base,
                bufsize=1
            )

        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if '%' in line:
                    match = re.search(r'(\d+)\s*%', line)
                    if match:
                        percent = int(match.group(1))
                        progress_callback(percent)
            process.stdout.close()

        process.wait()

        if process.returncode != 0:
            stderr_output = process.stderr.read() if process.stderr else "No stderr."
            self.log.error(f"7-Zip failed with code {process.returncode}: {stderr_output}")
            return False

        progress_callback(100)
        self.log.info(f"DIM file created with 7-Zip at: {zip_path}")
        return True

    def _zip_with_zipfile(self, zip_path: str, arc_base: str, progress_callback: Callable[[int], None]) -> bool:
        self.log.info(f"Attempting to generate DIM file using zipfile: {zip_path}")
        total_size = max(1, calculate_total_size(arc_base))
        size_zipped = 0

        ignore_names = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}

        with zipfile.ZipFile(
            zip_path, mode='w', compression=zipfile.ZIP_DEFLATED,
            compresslevel=6, strict_timestamps=False
        ) as zipf:
            for root, dirs, files in os.walk(arc_base):
                if not files and not dirs:
                    continue
                dirs.sort()
                files.sort()
                for fname in files:
                    if fname in ignore_names:
                        continue
                    file_path = os.path.join(root, fname)
                    if os.path.isfile(file_path) and not os.path.islink(file_path):
                        arcname = os.path.relpath(file_path, arc_base).replace(os.sep, '/')
                        zipf.write(file_path, arcname)
                        try:
                            size_zipped += os.path.getsize(file_path)
                            percent = int((size_zipped / total_size) * 100)
                            progress_callback(percent)
                        except OSError:
                            pass
        
        progress_callback(100)
        self.log.info(f"DIM file created with zipfile at: {zip_path}")
        return True


class PackagingWorker(QThread):
    """A thin QThread wrapper that runs the PackagingPipeline."""
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, spec: PackageSpec, parent=None):
        super().__init__(parent)
        self.pipeline = PackagingPipeline(spec)

    def run(self):
        callbacks = {
            'progress': self.progress.emit
        }
        success, message = self.pipeline.execute(callbacks)
        self.finished.emit(success, message)