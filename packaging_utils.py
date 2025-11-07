import os
import re
import shutil
import stat
import zipfile
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
from PIL import Image, ImageOps
from PySide6.QtCore import QThread, Signal

from logger_utils import get_logger
from utils import calculate_total_files

log = get_logger(__name__)


def prettify(elem):
    """Return a pretty-printed XML string for the Element."""
    rough_string = tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    pretty = reparsed.toprettyxml(indent="  ")
    return '\n'.join(pretty.split('\n')[1:])


def clean_support_directory(content_dir: str) -> bool:
    """Recursively delete the contents of the 'Runtime/Support' directory."""
    target_dir = os.path.join(content_dir, "Runtime", "Support")
    if not os.path.exists(target_dir):
        return True

    log.info("Attempting to clean Support Directory: %s", target_dir)

    def handle_remove_readonly(func, path, _):
        """Clear the readonly bit and re-attempt the removal."""
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception as e:
            log.error(f"Still failed to delete {path}. Reason: {e}")

    for name in os.listdir(target_dir):
        p = os.path.join(target_dir, name)
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.chmod(p, stat.S_IWRITE)
                os.unlink(p)
            elif os.path.isdir(p):
                shutil.rmtree(p, onerror=handle_remove_readonly)
        except Exception as e:
            log.error(f"Failed to delete {p}. Reason: {e}")
            return False

    log.info("Support directory successfully cleaned.")
    return True


def process_and_paste_image(content_dir, store, sku, product_name, image_path):
    """Process and save the product image to the 'Runtime/Support' directory."""
    if not image_path:
        log.info("No image path provided, skipping image processing.")
        return True

    log.info("Attempting to generate Product cover from: %s", image_path)
    try:
        sanitized_product_name = re.sub(r'[^A-Za-z0-9._-]+', '_', product_name).strip('_')
        store_formatted = re.sub(r'[^A-Za-z0-9._-]+', '_', store).strip('_')
        new_image_name = f"{store_formatted}_{sku}_{sanitized_product_name}.jpg"

        target_dir = os.path.join(content_dir, "Runtime", "Support")
        os.makedirs(target_dir, exist_ok=True)
        new_image_path = os.path.join(target_dir, new_image_name)

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB':
                img = img.convert("RGB")
            img.thumbnail((300, 300), Image.Resampling.LANCZOS)
            img.save(new_image_path, "JPEG")
            log.info("Product cover successfully generated at: %s", new_image_path)
        return True
    except Exception as e:
        log.error(f"An error occurred while processing the image: {e}")
        return False


def create_manifest(content_dir, guid):
    """Create the Manifest.dsx file."""
    log.info("Attempting to generate Product Manifest.")
    try:
        root = Element('DAZInstallManifest', VERSION="0.1")
        SubElement(root, 'GlobalID', VALUE=guid)

        for subdir, dirs, files in os.walk(content_dir):
            dirs.sort()
            files.sort()
            for file in files:
                file_path = os.path.join(subdir, file).replace("\\", "/")
                rel_path = os.path.relpath(file_path, start=content_dir).replace("\\", "/")
                SubElement(root, 'File', TARGET="Content", ACTION="Install", VALUE=f"Content/{rel_path}")

        xml_str = prettify(root)
        manifest_path = os.path.join(os.path.dirname(content_dir), "Manifest.dsx")
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as mf:
            mf.write(xml_str)
        log.info("Product Manifest successfully generated at: %s", manifest_path)
        return True
    except Exception as e:
        log.error(f"An error occurred while creating the manifest: {e}")
        return False


def create_supplement(content_dir, product_name, product_tags):
    """Create the Supplement.dsx file."""
    log.info("Attempting to generate Product Supplement.")
    try:
        root = Element('ProductSupplement', VERSION="0.1")
        SubElement(root, 'ProductName', VALUE=product_name)
        SubElement(root, 'InstallTypes', VALUE="Content")
        SubElement(root, 'ProductTags', VALUE=product_tags)

        xml_str = prettify(root)
        supplement_path = os.path.join(os.path.dirname(content_dir), "Supplement.dsx")
        with open(supplement_path, "w", encoding="utf-8", newline="\n") as supplement_file:
            supplement_file.write(xml_str)
        log.info("Product Supplement successfully generated at: %s", supplement_path)
        return True
    except Exception as e:
        log.error(f"An error occurred while creating the supplement: {e}")
        return False


def zip_package(
    content_dir, prefix, sku, product_part, product_name,
    destination_folder, report_progress
):
    """Create the final ZIP package."""
    prefix_clean = re.sub(r'[^A-Za-z0-9]+', '', str(prefix)).upper()
    try:
        sku_formatted = f"{int(str(sku)):08d}"
    except ValueError:
        sku_formatted = str(sku).zfill(8)

    part_str = f"{int(product_part):02d}"
    sanitized_name = re.sub(r'[^A-Za-z0-9._-]+', '_', str(product_name)).strip('_')
    zip_name = f"{prefix_clean}{sku_formatted}-{part_str}_{sanitized_name}.zip"
    zip_path = os.path.join(destination_folder, zip_name)

    arc_base = os.path.dirname(content_dir)
    total_files = max(1, calculate_total_files(arc_base))
    files_zipped = 0
    log.info("Attempting to generate the DIM file: %s", zip_path)

    ignore_names = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}

    with zipfile.ZipFile(
        zip_path, mode='w', compression=zipfile.ZIP_DEFLATED,
        compresslevel=9, strict_timestamps=False
    ) as zipf:
        for root, dirs, files in os.walk(arc_base):
            # Exclude empty directories from zipping
            if not files and not dirs:
                continue
                
            dirs.sort()
            files.sort()

            for fname in files:
                if fname in ignore_names:
                    continue
                file_path = os.path.join(root, fname)
                arcname = os.path.relpath(file_path, arc_base).replace(os.sep, '/')
                zipf.write(file_path, arcname)

                files_zipped += 1
                percent = int((files_zipped / total_files) * 100)
                report_progress(percent)

    report_progress(100)
    log.info(f"DIM file created at: {zip_path}")
    return True


class PackagingWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(bool, str)
    error = Signal(str)

    def __init__(self, content_dir, store, product_name, prefix, sku, product_part, product_tags, image_path, clean_support, guid, destination_folder, parent=None):
        super().__init__(parent)
        self.content_dir = content_dir
        self.store = store
        self.product_name = product_name
        self.prefix = prefix
        self.sku = sku
        self.product_part = product_part
        self.product_tags = product_tags
        self.image_path = image_path
        self.clean_support = clean_support
        self.guid = guid
        self.destination_folder = destination_folder

    def run(self):
        try:
            # Step 1: Clean support directory if requested
            if self.clean_support:
                self.progress.emit(5, "Cleaning")
                if not clean_support_directory(self.content_dir):
                    self.finished.emit(False, "Failed to clean the Support directory.")
                    return

            # Step 2: Process and paste image
            self.progress.emit(10, "Processing Image")
            if not process_and_paste_image(self.content_dir, self.store, self.sku, self.product_name, self.image_path):
                self.finished.emit(False, "Image processing failed.")
                return

            # Step 3: Create manifest
            self.progress.emit(15, "Creating Manifest")
            if not create_manifest(self.content_dir, self.guid):
                self.finished.emit(False, "Manifest creation failed.")
                return

            # Step 4: Create supplement
            self.progress.emit(20, "Creating Supplement")
            if not create_supplement(self.content_dir, self.product_name, self.product_tags):
                self.finished.emit(False, "Supplement creation failed.")
                return

            # Step 5: Zip everything
            def report_zip_progress(percent):
                # Scale zipping progress from 25% to 100%
                scaled_percent = 25 + int((percent / 100) * 75)
                self.progress.emit(scaled_percent, "Packaging")

            if not zip_package(
                self.content_dir, self.prefix, self.sku, self.product_part, self.product_name,
                self.destination_folder, report_zip_progress
            ):
                self.finished.emit(False, "Failed to create ZIP archive.")
                return

            self.finished.emit(True, "Packaging complete.")

        except Exception as e:
            log.exception("An unexpected error occurred during packaging.")
            self.error.emit(str(e))