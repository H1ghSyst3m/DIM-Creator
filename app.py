import sys
import os
import tempfile
import shutil
import zipfile
import stat
import uuid
import re
import patoolib
import ctypes
import shiboken6
import time
from datetime import date

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from qfluentwidgets import (
    setFont, PrimaryPushButton, PushButton, LineEdit, setTheme, Theme,
    EditableComboBox, CheckBox, InfoBarPosition, ProgressRing, ToolButton,
    StateToolTip, DropDownPushButton, RoundMenu, Action
)
from qfluentwidgets import FluentIcon as FIF
from PySide6.QtWidgets import (
    QMessageBox, QApplication, QWidget, QLabel, QDialog,
    QVBoxLayout, QFileDialog, QCompleter, QHBoxLayout,
    QGraphicsBlurEffect, QStackedLayout, QSizePolicy, QFormLayout,
    QSpacerItem
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QSettings, QTimer, QRegularExpression
)
from PySide6.QtGui import (
    QIcon, QKeySequence, QIntValidator, QRegularExpressionValidator,
    QShortcut
)
from concurrent.futures import ThreadPoolExecutor

from utils import (
    resource_path, documents_dir, downloads_dir, DOC_MAIN_DIR,
    suppress_cmd_window, get_optimal_workers,
    tooltip_stylesheet, label_stylesheet,
    show_error, show_info, show_success, show_warning,
    ensure_builds_directory_structure, create_build_folder,
    get_build_content_dir, get_build_dir, create_session_backup,
    SESSION_FILE, delete_session_file, delete_all_build_folders,
    IGNORE_SYSTEM_FILES, format_file_size
)
from logger_utils import get_logger
from widgets import (
    ProductLineEdit, TagSelectionDialog, CustomCompactSpinBox, ImageLabel,
    FileExplorer, BuildListWidget
)
from packaging_utils import PackagingWorker, PackageSpec, BatchPackagingWorker
from extraction_utils import (
    ContentExtractionWorker, MultiBuildExtractionWorker,
    classify_archives, detect_heuristic_ordering
)
from config_utils import load_configurations
from settings import SettingsDialog
from updater import UpdateManager
from version import APP_VERSION
from session import Build, save_session, load_session, create_default_session
from build_manager import (
    create_build, delete_build, get_build_data, validate_build,
    set_field_override, sync_to_children, sync_from_parent, get_effective_value
)
from dialogs import ExitDialog, ExtractionDialog, ValidationDialog, BatchProgressDialog, ResultSummaryDialog

log = get_logger(__name__)
log.info("Application starting...")

settings = QSettings("Syst3mApps", "DIMCreator")

documents_path = documents_dir()
doc_main_dir = DOC_MAIN_DIR
logo_path = resource_path(
    os.path.join('assets', 'images', 'logo', 'favicon.ico')
)


class DIMPackageGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.doc_main_dir = doc_main_dir
        (self.storeitems, self.store_prefixes, self.available_tags,
         self.daz_folders) = load_configurations(self.doc_main_dir)
        self.stateTooltip = None
        
        self.session = None
        self.current_build = None
        self._loading_build = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._debouncedSave)
        self.ensure_directory_structure()
        self.loadSession()
        
        setTheme(Theme.DARK)
        self.initUI()
        self.loadSettings()
        self.updateZipPreview()
        self.updater = UpdateManager(
            self, settings, current_version=APP_VERSION, interval_hours=24
        )
        self.updater.schedule_on_startup_if_enabled()
        QTimer.singleShot(0, self.updateSourcePrefixBasedOnStore)
        self._extractionHadError = False

    def loadSettings(self):
        self.prefix_input.setText(
            settings.value("prefix_input", "", type=str)
        )
        self.product_tags_input.setText(
            settings.value("product_tags_input", "DAZStudio4_5", type=str)
        )
        self.last_destination_folder = settings.value(
            "last_destination_folder", os.path.expanduser("~"), type=str
        )
        self.enable_template_detection = settings.value(
            "enable_template_detection", False, type=bool
        )
        self.template_destination = settings.value(
            "template_destination", "", type=str
        )

        saved_store = settings.value("store_input", "", type=str)
        if saved_store:
            index = self.store_input.findText(saved_store)
            if index >= 0:
                self.store_input.setCurrentIndex(index)
            else:
                log.warning(
                    f"Saved store '{saved_store}' not found in available "
                    "stores, using default."
                )
        self.use_store_prefix_checkbox.setChecked(
            settings.value("auto_prefix", False, type=bool)
        )

    def saveSettings(self):
        settings.setValue("prefix_input", self.prefix_input.text())
        settings.setValue("product_tags_input", self.product_tags_input.text())
        settings.setValue("last_destination_folder", self.last_destination_folder)
        settings.setValue("store_input", self.store_input.currentText())
        settings.setValue("auto_prefix", self.use_store_prefix_checkbox.isChecked())

    def hasUserMadeChanges(self) -> bool:
        if not self.session or not self.session.builds:
            return False
        
        if len(self.session.builds) > 1:
            return True
        
        for build in self.session.builds:
            product_name = get_effective_value(self.session, build, 'product_name')
            if product_name and product_name.strip():
                return True
            
            sku = get_effective_value(self.session, build, 'sku')
            if sku and sku.strip():
                return True
            
            image_path = get_effective_value(self.session, build, 'image_path')
            if image_path and image_path.strip():
                return True
            
            try:
                content_dir = get_build_content_dir(build.folder)
                if os.path.exists(content_dir):
                    entries = [e for e in os.listdir(content_dir) if e not in IGNORE_SYSTEM_FILES]
                    if entries:
                        return True
            except OSError as e:
                log.warning(f"Error checking content directory for {build.folder}: {e}")
        
        return False

    def performSessionCleanup(self) -> tuple[bool, list[str]]:
        try:
            if hasattr(self, '_save_timer') and self._save_timer is not None:
                self._save_timer.stop()
            
            log.info("Cleaning up all builds...")
            failed = delete_all_build_folders(handle_error_callback=self.handle_remove_readonly)
            
            if failed:
                log.warning(f"Some build folders failed to delete: {failed}")
            
            delete_session_file()
            
            log.info("Cleanup complete")
            return (True, failed)
        except Exception as e:
            log.error(f"Error during cleanup: {e}")
            return (False, [])
    
    def onNewSession(self):
        reply = QMessageBox.question(
            self,
            "Start New Session?",
            "This will delete all builds and content. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        preserved_store = self.store_input.currentText() if hasattr(self, 'store_input') else ""
        preserved_prefix = self.prefix_input.text() if hasattr(self, 'prefix_input') else ""
        
        success, failed = self.performSessionCleanup()
        
        if not success:
            show_error(self, "Cleanup Error", "Failed to clean up session")
            return
        
        if failed:
            show_warning(
                self,
                "Partial Cleanup",
                f"Some build folders could not be deleted: {', '.join(failed)}\n\nCreating new session anyway."
            )
        
        if hasattr(self.session, 'builds'):
            self.session.builds.clear()
        
        self.session = create_default_session()
        first_build = self.session.builds[0]
        
        if preserved_store:
            first_build.store = preserved_store
        if preserved_prefix:
            first_build.prefix = preserved_prefix
        
        create_build_folder(first_build.folder)
        
        self.current_build = first_build
        
        if hasattr(self, 'buildListWidget'):
            self.buildListWidget.setSession(self.session)
            self.buildListWidget.selectBuild(first_build.id)
        
        self.loadBuildIntoEditor(first_build)
        
        content_dir = get_build_content_dir(first_build.folder)
        if hasattr(self, 'fileExplorer'):
            self.fileExplorer.reset_model()
            self.fileExplorer.setRootPath(content_dir)
        
        self.saveSession()
        
        if hasattr(self, '_save_timer') and self._save_timer is not None:
            if not self._save_timer.isActive():
                self._save_timer.start()
        
        show_success(self, "New Session", "Created new session with Build 1")
        log.info("New session created successfully")

    def closeEvent(self, event):
        show_dialog = self.hasUserMadeChanges()
        
        if show_dialog:
            dialog = ExitDialog(self)
            dialog.exec()
            result = dialog.getResult()
            
            if result == ExitDialog.RESULT_CANCEL:
                event.ignore()
                return
            elif result == ExitDialog.RESULT_CLEAN:
                success, failed = self.performSessionCleanup()
                
                if not success:
                    show_error(self, "Cleanup Error", "Failed to clean up session")
                elif failed:
                    show_warning(
                        self,
                        "Partial Cleanup",
                        f"Some build folders could not be deleted: {', '.join(failed)}"
                    )
        
        try:
            self.package_all_button.setEnabled(False)
            self.package_selected_button.setEnabled(False)
            self.extract_button.setEnabled(False)
        except Exception:
            pass

        try:
            for attr in ("stateTooltip", "_finalTip"):
                tip = getattr(self, attr, None)
                if tip:
                    try:
                        if shiboken6.isValid(tip):
                            tip.close()
                            tip.deleteLater()
                    except Exception:
                        pass
                    setattr(self, attr, None)
        except Exception:
            pass

        for attr in ("packaging_worker", "extractionWorker"):
            t = getattr(self, attr, None)
            try:
                if t and t.isRunning():
                    t.requestInterruption()
                    t.wait(5000)
            except Exception:
                pass

        try:
            t = getattr(getattr(self, "updater", None), "_thread", None)
            if t and t.isRunning():
                t.requestInterruption()
                t.wait(3000)
        except Exception:
            pass

        try:
            self.progress_ring.hide()
            self.progress_ring.setValue(0)
        except Exception:
            pass

        self.saveSettings()
        self.cleanUpTemporaryImage()
        
        if show_dialog:
            if result == ExitDialog.RESULT_SAVE:
                self.saveSession()
        else:
            self.saveSession()

        super().closeEvent(event)

    def ensure_directory_structure(self):
        ensure_builds_directory_structure()
    
    def loadSession(self):
        self.session = load_session(SESSION_FILE)
        
        if self.session is None:
            log.info("No session found, creating new session with Build 1")
            self.session = create_default_session()
            
            create_build_folder(self.session.builds[0].folder)
            
            self.saveSession()
        else:
            log.info(f"Session loaded with {len(self.session.builds)} builds")
            
            for build in self.session.builds:
                content_dir = get_build_content_dir(build.folder)
                if not os.path.exists(content_dir):
                    log.warning(f"Build folder missing: {build.folder}, recreating")
                    create_build_folder(build.folder)
        
        if self.session.builds:
            if 0 <= self.session.last_selected_build < len(self.session.builds):
                self.current_build = self.session.builds[self.session.last_selected_build]
            else:
                self.current_build = self.session.builds[0]
    
    def saveSession(self):
        if self.session:
            try:
                create_session_backup()
                
                save_session(self.session, SESSION_FILE)
                log.info("Session saved successfully")
            except Exception as e:
                log.error(f"Failed to save session: {e}")
                show_error(self, "Session Save Error", f"Failed to save session: {e}")
    
    def onBuildSelected(self, build_id: str):
        for i, build in enumerate(self.session.builds):
            if build.id == build_id:
                self.current_build = build
                self.session.last_selected_build = i
                
                self.loadBuildIntoEditor(build)
                
                content_dir = get_build_content_dir(build.folder)
                if hasattr(self, 'fileExplorer'):
                    self.fileExplorer.setRootPath(content_dir)
                
                self.saveSession()
                break
    
    def onBuildAdded(self):
        try:
            new_build = create_build(self.session)
            
            self.buildListWidget.refreshList()
            
            self.buildListWidget.selectBuild(new_build.id)
            
            self.saveSession()
            
            show_info(self, "Build Added", f"Build {new_build.part:02d} created")
        except Exception as e:
            log.error(f"Failed to create build: {e}")
            show_error(self, "Error", f"Failed to create build: {e}")
    
    def onBuildDeleted(self, build_id: str):
        try:
            delete_build(self.session, build_id)
            
            self.buildListWidget.refreshList()
            
            if self.session.builds:
                self.buildListWidget.selectBuild(self.session.builds[0].id)
            
            self.saveSession()
            
            show_info(self, "Build Deleted", "Build deleted successfully")
        except Exception as e:
            log.error(f"Failed to delete build: {e}")
            show_error(self, "Error", f"Failed to delete build: {e}")
    
    def onBuildsReordered(self):
        if self.current_build:
            found = False
            for i, build in enumerate(self.session.builds):
                if build.id == self.current_build.id:
                    self.current_build = build
                    self.session.last_selected_build = i
                    self.loadBuildIntoEditor(build)
                    found = True
                    break
            
            if not found and self.session.builds:
                log.warning(f"Current build {self.current_build.id} not found after reorder, selecting first build")
                self.current_build = self.session.builds[0]
                self.session.last_selected_build = 0
                self.loadBuildIntoEditor(self.current_build)
        
        self.saveSession()
        
        log.info("Build order updated and saved")
    
    def loadBuildIntoEditor(self, build: Build):
        self._loading_build = True
        try:
            build_data = get_build_data(self.session, build)
            
            store = build_data.get('store', '')
            if store:
                index = self.store_input.findText(store)
                if index >= 0:
                    self.store_input.setCurrentIndex(index)
            else:
                self.store_input.setCurrentIndex(-1)
                self.store_input.setCurrentText('')
            
            self.product_name_input.setText(build_data.get('product_name', ''))
            
            self.prefix_input.setText(build_data.get('prefix', ''))
            
            self.sku_input.setText(build_data.get('sku', ''))
            
            if hasattr(self, 'product_part_input'):
                self.product_part_input.setValue(build.part)
                self.updateBuildNumberEditability()
            
            if hasattr(self, 'guid_input'):
                self.guid_input.setText(build.guid)
            
            self.product_tags_input.setText(build_data.get('tags', 'DAZStudio4_5'))
            
            image_path = build_data.get('image_path', '')
            if image_path and os.path.exists(image_path) and hasattr(self, 'image_label'):
                self.image_label.setImagePath(image_path)
            elif hasattr(self, 'image_label'):
                self.image_label.removeImage()
            
            self.updateZipPreview()
            
            self.updateSyncControlsVisibility()
        finally:
            self._loading_build = False
    
    def saveBuildFieldChanges(self):
        if self._loading_build:
            return
        if not self.current_build or not self.session:
            return
        
        set_field_override(self.session, self.current_build, 'store', self.store_input.currentText())
        set_field_override(self.session, self.current_build, 'product_name', self.product_name_input.text())
        set_field_override(self.session, self.current_build, 'prefix', self.prefix_input.text())
        set_field_override(self.session, self.current_build, 'sku', self.sku_input.text())
        set_field_override(self.session, self.current_build, 'tags', self.product_tags_input.text())
        
        self.current_build.guid = self.guid_input.text() if hasattr(self, 'guid_input') else self.current_build.guid
        
        if hasattr(self, 'image_label'):
            image_path = self.image_label.imagePath
            if image_path:
                set_field_override(self.session, self.current_build, 'image_path', image_path)
            elif self.current_build.part > 1 and 'image_path' in self.current_build.overrides:
                del self.current_build.overrides['image_path']
        
        content_dir = get_build_content_dir(self.current_build.folder)
        effective_data = get_build_data(self.session, self.current_build)
        
        previous_status = getattr(self.current_build, "content_status", None)
        
        self.current_build.content_status = validate_build(
            self.current_build, 
            content_dir, 
            self.daz_folders,
            effective_values=effective_data
        )
        
        status_changed = self.current_build.content_status != previous_status
        if self.current_build.part == 1 and len(self.session.builds) > 1:
            for build in self.session.builds:
                if build.part > 1:
                    child_content_dir = get_build_content_dir(build.folder)
                    child_effective_data = get_build_data(self.session, build)
                    child_previous_status = build.content_status
                    build.content_status = validate_build(
                        build,
                        child_content_dir,
                        self.daz_folders,
                        effective_values=child_effective_data
                    )
                    if build.content_status != child_previous_status:
                        status_changed = True
        
        if hasattr(self, 'buildListWidget') and status_changed:
            self.buildListWidget.refreshList()
        
        self._save_timer.start()
    
    def _debouncedSave(self):
        self.saveSession()
    
    def _revalidateAllBuildsStatus(self):
        if not self.session or not self.session.builds:
            return
        
        part1_build = None
        for build in self.session.builds:
            if build.part == 1:
                part1_build = build
                break
        
        if not part1_build:
            for build in self.session.builds:
                content_dir = get_build_content_dir(build.folder)
                effective_data = get_build_data(self.session, build)
                build.content_status = validate_build(
                    build,
                    content_dir,
                    self.daz_folders,
                    effective_values=effective_data
                )
            return
        
        part1_content_dir = get_build_content_dir(part1_build.folder)
        part1_effective_data = get_build_data(self.session, part1_build)
        part1_build.content_status = validate_build(
            part1_build,
            part1_content_dir,
            self.daz_folders,
            effective_values=part1_effective_data
        )
        
        if len(self.session.builds) > 1:
            for build in self.session.builds:
                if build.part > 1:
                    child_content_dir = get_build_content_dir(build.folder)
                    child_effective_data = get_build_data(self.session, build)
                    build.content_status = validate_build(
                        build,
                        child_content_dir,
                        self.daz_folders,
                        effective_values=child_effective_data
                    )
    
    def updateBuildNumberEditability(self):
        if not hasattr(self, 'product_part_input') or not self.session:
            return
        
        build_count = len(self.session.builds)
        
        if build_count > 1:
            self.product_part_input.setReadOnly(True)
        else:
            self.product_part_input.setReadOnly(False)
    
    def updateSyncControlsVisibility(self):
        if not hasattr(self, 'sync_container_widget') or not self.current_build or not self.session:
            return
        
        if len(self.session.builds) <= 1:
            self.sync_container_widget.hide()
            return
        
        self.sync_container_widget.show()
        
        if self.current_build.part == 1:
            self.sync_from_build1_button.hide()
            self.sync_to_all_button.show()
        else:
            self.sync_from_build1_button.show()
            self.sync_to_all_button.hide()
    
    def onSyncFromBuild1(self):
        if not self.current_build or not self.session:
            return
        
        if self.current_build.part == 1:
            return
        
        try:
            sync_from_parent(self.session, self.current_build.id)
            
            content_dir = get_build_content_dir(self.current_build.folder)
            effective_data = get_build_data(self.session, self.current_build)
            
            previous_status = self.current_build.content_status
            self.current_build.content_status = validate_build(
                self.current_build,
                content_dir,
                self.daz_folders,
                effective_values=effective_data
            )
            
            if hasattr(self, 'buildListWidget') and self.current_build.content_status != previous_status:
                self.buildListWidget.refreshList()
            
            self.loadBuildIntoEditor(self.current_build)
            
            self.saveSession()
            
            show_success(
                self,
                "Synced",
                f"Build {self.current_build.part} has been synced from Build 1."
            )
        except Exception as e:
            log.error(f"Error syncing from Build 1: {e}")
            show_error(self, "Sync Failed", f"Failed to sync from Build 1: {str(e)}")
    
    def onSyncToAll(self, field_name: str):
        if not self.current_build or not self.session:
            return
        
        if self.current_build.part != 1:
            return
        
        child_count = len([b for b in self.session.builds if b.part > 1])
        
        if child_count == 0:
            show_info(self, "No Children", "There are no child parts to sync to.")
            return
        
        field_display_names = {
            "all": "All Fields",
            "store": "Store",
            "product_name": "Product Name",
            "prefix": "Prefix",
            "sku": "SKU",
            "tags": "Tags",
            "image_path": "Image Path"
        }
        field_display = field_display_names.get(field_name, field_name)
        
        reply = QMessageBox.question(
            self,
            "Confirm Sync",
            f"This will overwrite {field_display.lower()} in {child_count} child build(s) with values from Build 1.\n\n"
            f"Any customized values will be lost. Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        try:
            self._save_timer.stop()
            self.saveBuildFieldChanges()
            self._save_timer.stop()
            
            if field_name == "all":
                sync_to_children(self.session, field=None)
            else:
                sync_to_children(self.session, field=field_name)
            
            for build in self.session.builds:
                if build.part > 1:
                    content_dir = get_build_content_dir(build.folder)
                    effective_data = get_build_data(self.session, build)
                    build.content_status = validate_build(
                        build,
                        content_dir,
                        self.daz_folders,
                        effective_values=effective_data
                    )
            
            if hasattr(self, 'buildListWidget'):
                self.buildListWidget.refreshList()
            
            self.saveSession()
            
            show_success(
                self,
                "Synced",
                f"{field_display} synced to {child_count} child part(s)."
            )
        except Exception as e:
            log.error(f"Error syncing to all parts: {e}")
            show_error(self, "Sync Failed", f"Failed to sync to all parts: {str(e)}")
    
    def onGuidChanged(self):
        if self._loading_build:
            return
        if self.current_build and self.session:
            self.saveBuildFieldChanges()
    
    def onImageChanged(self, image_path):
        if self._loading_build:
            return
        if self.current_build and self.session:
            self.saveBuildFieldChanges()

    def cleanUpTemporaryImage(self):
        try:
            if getattr(self, 'image_label', None) and self.image_label.imagePath:
                if getattr(self.image_label, "_ownedTemp", False):
                    image_path = self.image_label.imagePath
                    try:
                        os.remove(image_path)
                        log.info(f"Temporary image file deleted: {image_path}")
                    except OSError as e:
                        log.error(f"Error deleting temporary image file '{image_path}': {e}")
                self.image_label.removeImage()
        except Exception as e:
            log.error(f"cleanUpTemporaryImage failed: {e}")

    def openTagSelectionDialog(self):
        selected_tags = self.product_tags_input.text().split(",")

        dialog = TagSelectionDialog(self.available_tags, selected_tags, self)
        if dialog.exec() == QDialog.Accepted:
            selected_tags = dialog.getSelectedTags()
            self.product_tags_input.setText(",".join(selected_tags))

    def updateTagsInput(self, tag, checked):
        current_tags = self.product_tags_input.text().split(',')
        if checked and tag not in current_tags:
            current_tags.append(tag)
        elif not checked and tag in current_tags:
            current_tags.remove(tag)
        self.product_tags_input.setText(','.join(current_tags))

    def updateSourcePrefixBasedOnStore(self):
        use_store_prefix = self.use_store_prefix_checkbox.isChecked()
        self.prefix_input.setEnabled(not use_store_prefix)

        if use_store_prefix:
            selected_store = self.store_input.currentText()
            store_prefix = self.store_prefixes.get(selected_store, "")
            self.prefix_input.setText(store_prefix)

        self.updateZipPreview()

    def build_zip_filename(self) -> str:
        prefix_raw = self.prefix_input.text() or "IM"
        sku_raw = self.sku_input.text() or ""
        part_val = self.product_part_input.value()
        name_raw = self.product_name_input.text() or "Package"

        prefix_clean = re.sub(r'[^A-Za-z0-9]+', '', str(prefix_raw)).upper() or "IM"
        try:
            sku_formatted = f"{int(str(sku_raw)):08d}"
        except ValueError:
            sku_formatted = (str(sku_raw) or "").zfill(8) if sku_raw else "00000000"
        part_str = f"{int(part_val):02d}"
        sanitized_name = re.sub(r'[^A-Za-z0-9._-]+', '_', str(name_raw)).strip('_') or "Package"

        return f"{prefix_clean}{sku_formatted}-{part_str}_{sanitized_name}.zip"

    def updateZipPreview(self):
        try:
            if hasattr(self, 'zip_preview_edit'):
                self.zip_preview_edit.setText(self.build_zip_filename())
                self.zip_preview_edit.setCursorPosition(0)
        except Exception:
            pass

    def _setImageBusy(self, busy: bool, text: str = "Processing…", percent: int | None = None):
        try:
            if busy:
                self.progress_ring.setValue(0)

                if text:
                    self._overlay_text.setText(text)
                if percent is not None:
                    self.progress_ring.setValue(max(0, min(100, percent)))

                eff = QGraphicsBlurEffect(self.image_label)
                eff.setBlurRadius(12)
                self._current_blur = eff
                self.image_label.setGraphicsEffect(eff)

                self._image_overlay.show()
                self._image_overlay.raise_()
            else:
                self._image_overlay.hide()

                eff = getattr(self, "_current_blur", None)
                if eff is not None:
                    self.image_label.setGraphicsEffect(None)
                    try:
                        eff.deleteLater()
                    except Exception:
                        pass
                    self._current_blur = None

                self.progress_ring.setValue(0)
        except Exception:
            pass

    def initUI(self):

        self.setWindowTitle("DIMCreator")
        self.setMinimumSize(1010, 800)
        self.setStyleSheet(tooltip_stylesheet + "DIMPackageGUI{background: rgb(32, 32, 32)}")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        main = QHBoxLayout()
        main.setSpacing(14)
        root.addLayout(main, stretch=0)
        
        self.buildListWidget = BuildListWidget(self)
        self.buildListWidget.setSession(self.session)
        self.buildListWidget.setMinimumWidth(180)
        self.buildListWidget.setMaximumWidth(250)
        self.buildListWidget.buildSelected.connect(self.onBuildSelected)
        self.buildListWidget.buildAdded.connect(self.onBuildAdded)
        self.buildListWidget.buildCheckedChanged.connect(self.onBuildCheckedChanged)
        self.buildListWidget.buildDeleted.connect(self.onBuildDeleted)
        self.buildListWidget.buildsReordered.connect(self.onBuildsReordered)
        main.addWidget(self.buildListWidget)

        left_wrap = QWidget(self)
        main.addWidget(left_wrap, 1)

        form = QFormLayout(left_wrap)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        def L(text):
            lbl = QLabel(text, self)
            lbl.setStyleSheet(label_stylesheet)
            return lbl

        self.store_input = EditableComboBox(self)
        self.store_input.addItems(self.storeitems)
        self.store_completer = QCompleter(self.storeitems, self)
        self.store_input.setCompleter(self.store_completer)
        self.store_input.setMaxVisibleItems(10)
        self.store_input.setToolTip("Select the store from which the product was purchased.")
        self.store_input.currentIndexChanged.connect(self.updateSourcePrefixBasedOnStore)
        form.addRow(L("Store:"), self.store_input)

        prefix_row = QWidget(self)
        pr_h = QHBoxLayout(prefix_row)
        pr_h.setContentsMargins(0, 0, 0, 0)
        pr_h.setSpacing(8)
        self.prefix_input = LineEdit(self)
        self.prefix_input.setClearButtonEnabled(True)
        self.prefix_input.setPlaceholderText("IM")
        self.prefix_input.setToolTip("Enter the source prefix, typically the vendor's initials.")
        self.use_store_prefix_checkbox = CheckBox("Auto Prefix", self)
        self.use_store_prefix_checkbox.stateChanged.connect(self.updateSourcePrefixBasedOnStore)
        self.prefix_input.setEnabled(not self.use_store_prefix_checkbox.isChecked())
        self.prefix_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pr_h.addWidget(self.prefix_input, 1)
        pr_h.addWidget(self.use_store_prefix_checkbox, 0)
        form.addRow(L("Source Prefix:"), prefix_row)

        self.product_name_input = ProductLineEdit(self)
        self.product_name_input.setClearButtonEnabled(True)
        self.product_name_input.setPlaceholderText("dForce Starter Essentials")
        self.product_name_input.setToolTip("Enter the name of the product.")
        form.addRow(L("Product Name:"), self.product_name_input)

        sku_row = QWidget(self)
        sku_h = QHBoxLayout(sku_row)
        sku_h.setContentsMargins(0, 0, 0, 0)
        sku_h.setSpacing(8)
        self.sku_input = LineEdit(self)
        self.sku_input.setClearButtonEnabled(True)
        self.sku_input.setPlaceholderText("47939")
        self.sku_input.setMaxLength(8)
        self.sku_input.setValidator(QIntValidator(0, 99999999, self))
        self.sku_input.setToolTip(
            "Enter the SKU (Stock Keeping Unit) for the package."
        )
        dash_lbl = QLabel("-", self)
        dash_lbl.setStyleSheet(label_stylesheet)
        self.product_part_input = CustomCompactSpinBox(self)
        self.product_part_input.setRange(1, 99)
        self.product_part_input.setValue(1)
        sku_h.addWidget(self.sku_input, 1)
        sku_h.addWidget(dash_lbl, 0)
        sku_h.addWidget(self.product_part_input, 0)
        form.addRow(L("Package SKU:"), sku_row)

        guid_row = QWidget(self)
        guid_h = QHBoxLayout(guid_row)
        guid_h.setContentsMargins(0, 0, 0, 0)
        guid_h.setSpacing(8)
        self.guid_input = LineEdit(self)
        self.guid_input.setClearButtonEnabled(True)
        self.guid_input.setPlaceholderText("a4a82911-662e-4e02-8416-b7b8c0f7d4a4")
        self.guid_input.setToolTip(
            "This is a unique identifier for the package. Click the "
            "generate button to create one."
        )
        self.guid_input.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'),
                self
            )
        )
        self.generate_guid_button = ToolButton(FIF.ADD, self)
        self.generate_guid_button.clicked.connect(self.generateGUID)
        self.generate_guid_button.setToolTip("Click to create a random GUID.")
        guid_h.addWidget(self.guid_input, 1)
        guid_h.addWidget(self.generate_guid_button, 0)
        form.addRow(L("Package GUID:"), guid_row)

        tags_row = QWidget(self)
        tags_h = QHBoxLayout(tags_row)
        tags_h.setContentsMargins(0, 0, 0, 0)
        tags_h.setSpacing(8)
        self.product_tags_input = LineEdit(self)
        self.product_tags_input.setClearButtonEnabled(True)
        self.product_tags_input.setToolTip(
            "Click the Tag button to select product tags that apply."
        )
        self.tags_button = ToolButton(FIF.TAG, self)
        self.tags_button.clicked.connect(self.openTagSelectionDialog)
        self.tags_button.setToolTip(
            "Click to select product tags that apply."
        )
        tags_h.addWidget(self.product_tags_input, 1)
        tags_h.addWidget(self.tags_button, 0)
        form.addRow(L("Product Tags:"), tags_row)

        opts_row = QWidget(self)
        opts_h = QHBoxLayout(opts_row)
        opts_h.setContentsMargins(0, 0, 0, 0)
        opts_h.setSpacing(8)
        self.support_clean_input = CheckBox("Clean Support Directory", self)
        self.support_clean_input.setChecked(True)
        opts_h.addWidget(self.support_clean_input, 0)
        opts_h.addStretch(1)
        form.addRow(L("Options:"), opts_row)

        sync_row = QWidget(self)
        sync_h = QHBoxLayout(sync_row)
        sync_h.setContentsMargins(0, 0, 0, 0)
        sync_h.setSpacing(8)
        
        self.sync_from_build1_button = PushButton("Sync from Build 1", self)
        self.sync_from_build1_button.clicked.connect(self.onSyncFromBuild1)
        self.sync_from_build1_button.setToolTip(
            "Pull the latest field values from Build 1 and apply them to this build."
        )
        sync_h.addWidget(self.sync_from_build1_button, 0)
        
        self.sync_to_all_button = DropDownPushButton("Sync to All Builds", self)
        self.sync_to_all_button.setToolTip(
            "Push field values from Build 1 to all child builds."
        )
        self.sync_menu = RoundMenu(parent=self)
        self.sync_menu.addAction(Action(FIF.SYNC, "Sync All Fields", triggered=lambda: self.onSyncToAll("all")))
        self.sync_menu.addAction(Action(FIF.SHOPPING_CART, "Sync Store Only", triggered=lambda: self.onSyncToAll("store")))
        self.sync_menu.addAction(Action(FIF.TAG, "Sync Product Name Only", triggered=lambda: self.onSyncToAll("product_name")))
        self.sync_menu.addAction(Action(FIF.EDIT, "Sync Prefix Only", triggered=lambda: self.onSyncToAll("prefix")))
        self.sync_menu.addAction(Action(FIF.LABEL, "Sync SKU Only", triggered=lambda: self.onSyncToAll("sku")))
        self.sync_menu.addAction(Action(FIF.TAG, "Sync Tags Only", triggered=lambda: self.onSyncToAll("tags")))
        self.sync_menu.addAction(Action(FIF.PHOTO, "Sync Image Path Only", triggered=lambda: self.onSyncToAll("image_path")))
        self.sync_to_all_button.setMenu(self.sync_menu)
        sync_h.addWidget(self.sync_to_all_button, 0)
        
        sync_h.addStretch(1)
        
        form.addRow("", sync_row)
        self.sync_container_widget = sync_row

        actions_row = QWidget(self)
        actions_h = QHBoxLayout(actions_row)
        actions_h.setContentsMargins(0, 0, 0, 0)
        actions_h.setSpacing(8)
        
        self.package_all_button = PrimaryPushButton("Package All", self)
        self.package_all_button.clicked.connect(self.packageAllBuilds)
        self.package_all_button.setToolTip(
            "Package all builds in the session sequentially."
        )
        
        self.package_selected_button = PushButton("Package Selected", self)
        self.package_selected_button.clicked.connect(self.packageSelectedBuilds)
        self.package_selected_button.setToolTip(
            "Package only the checked builds in the build list."
        )
        self.package_selected_button.setEnabled(False)
        
        self.clear_button = ToolButton(FIF.ERASE_TOOL, self)
        self.clear_button.clicked.connect(self.clearAll)
        self.clear_button.setToolTip(
            "Clear all input fields and clean the current build's Content folder."
        )
        actions_h.addWidget(self.package_all_button, 0)
        actions_h.addWidget(self.package_selected_button, 0)
        actions_h.addWidget(self.clear_button, 0)
        actions_h.addStretch(1)
        form.addRow(L("Actions:"), actions_row)

        form.addItem(
            QSpacerItem(0, 24, QSizePolicy.Minimum, QSizePolicy.Fixed)
        )

        prev_row = QWidget(self)
        prev_h = QHBoxLayout(prev_row)
        prev_h.setContentsMargins(0, 0, 0, 0)
        prev_h.setSpacing(8)

        self.zip_preview_edit = LineEdit(self)
        self.zip_preview_edit.setReadOnly(True)
        self.zip_preview_edit.setMinimumWidth(260)
        self.zip_preview_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.zip_preview_edit.setCursorPosition(0)
        self.zip_preview_edit.setToolTip("Live preview of the final ZIP filename.")

        f = self.zip_preview_edit.font()
        f.setFamilies(["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Menlo", f.family()])
        self.zip_preview_edit.setFont(f)

        self.zip_preview_edit.textChanged.connect(lambda s: self.zip_preview_edit.setToolTip(s))

        copy_btn = ToolButton(FIF.COPY, self)
        copy_btn.setToolTip("Copy filename to clipboard")

        def _copy_preview():
            QApplication.clipboard().setText(self.zip_preview_edit.text())
            show_info(self, "Copied", "Filename copied to clipboard.")

        copy_btn.clicked.connect(_copy_preview)

        prev_h.addWidget(self.zip_preview_edit, 1)
        prev_h.addWidget(copy_btn, 0)

        form.addRow(L("Preview:"), prev_row)

        right_wrap = QWidget(self)
        right = QVBoxLayout(right_wrap)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)

        image_container = QWidget(right_wrap)
        stack = QStackedLayout(image_container)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.image_label = ImageLabel(image_container)
        self.image_label.setToolTip("Drop an image here or click to select an image file.")
        self.image_label.setMinimumSize(300, 320)
        self.image_label.setMaximumWidth(400)
        stack.addWidget(self.image_label)

        self._image_overlay = QWidget(image_container)
        self._image_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._image_overlay.setStyleSheet("background: transparent;")
        ov = QVBoxLayout(self._image_overlay)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setAlignment(Qt.AlignCenter)

        self.progress_ring = ProgressRing(self._image_overlay)
        self.progress_ring.setFixedSize(70, 70)
        self.progress_ring.setTextVisible(True)
        self.progress_ring.setValue(0)
        setFont(self.progress_ring, fontSize=13)

        self._overlay_text = QLabel("Working…", self._image_overlay)
        self._overlay_text.setStyleSheet("color: white; font-size: 10pt;")
        self._overlay_text.setAlignment(Qt.AlignHCenter)

        ov.addWidget(self.progress_ring, 0, Qt.AlignCenter)
        ov.addSpacing(8)
        ov.addWidget(self._overlay_text, 0, Qt.AlignCenter)

        stack.addWidget(self._image_overlay)
        self._image_overlay.hide()

        right.addWidget(image_container, 1)
        main.addWidget(right_wrap, 0)

        util_bar = QHBoxLayout()
        util_bar.setContentsMargins(0, 0, 0, 0)
        util_bar.setSpacing(8)

        left_tools = QHBoxLayout()
        left_tools.setSpacing(8)
        self.always_on_top_button = ToolButton(FIF.PIN, self)
        self.always_on_top_button.setCheckable(True)
        self.always_on_top_button.clicked.connect(self.toggleAlwaysOnTop)
        self.always_on_top_button.setToolTip("Toggle Always on Top")

        self.settings_button = ToolButton(FIF.SETTING, self)
        self.settings_button.clicked.connect(self.showSettingsDialog)
        self.settings_button.setToolTip("Open Settings Window")

        self.update_button = ToolButton(FIF.SYNC, self)
        self.update_button.setToolTip("Check for Updates")
        self.update_button.clicked.connect(lambda: self.updater.manual_check())

        for b in (self.always_on_top_button, self.settings_button, self.update_button):
            left_tools.addWidget(b)
        util_bar.addLayout(left_tools)

        util_bar.addStretch(1)

        self.extract_button = PushButton("Extract Archive", self)
        self.extract_button.clicked.connect(self.extractArchive)
        self.extract_button.setToolTip("Extract an archive into the Content folder (.zip .rar .7z).")
        util_bar.addWidget(self.extract_button)

        root.addLayout(util_bar)

        if self.current_build:
            content_dir = get_build_content_dir(self.current_build.folder)
        else:
            content_dir = ""
        
        self.fileExplorer = FileExplorer(content_dir, self, main_gui=self)
        self.fileExplorer.setMinimumHeight(260)
        root.addWidget(self.fileExplorer, 1)

        QShortcut(QKeySequence("Ctrl+G"), self, self.generateGUID)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.process)
        QShortcut(QKeySequence("Ctrl+N"), self, self.clearAll)

        self.prefix_input.textChanged.connect(self.updateZipPreview)
        self.sku_input.textChanged.connect(self.updateZipPreview)
        self.product_name_input.textChanged.connect(self.updateZipPreview)
        self.product_part_input.valueChanged.connect(
            lambda *_: self.updateZipPreview()
        )
        
        self.store_input.currentTextChanged.connect(lambda: self.saveBuildFieldChanges())
        self.prefix_input.textChanged.connect(lambda: self.saveBuildFieldChanges())
        self.product_name_input.textChanged.connect(lambda: self.saveBuildFieldChanges())
        self.sku_input.textChanged.connect(lambda: self.saveBuildFieldChanges())
        self.product_tags_input.textChanged.connect(lambda: self.saveBuildFieldChanges())
        self.guid_input.textChanged.connect(self.onGuidChanged)
        self.image_label.imageChanged.connect(self.onImageChanged)
        
        if self.current_build:
            self.loadBuildIntoEditor(self.current_build)
            content_dir = get_build_content_dir(self.current_build.folder)
            self.fileExplorer.setRootPath(content_dir)

    def showSettingsDialog(self):
        dialog = SettingsDialog(self.doc_main_dir, self)

        dialog.enable_template_detection_checkbox.setChecked(self.enable_template_detection)
        dialog.template_destination_field.setText(self.template_destination)
        
        output_org = settings.value("output_organization", "Flat", type=str)
        output_org_index = 0 if output_org == "Flat" else 1
        dialog.output_org_combo.setCurrentIndex(output_org_index)
        
        dialog.auto_update_checkbox.setChecked(
            settings.value("auto_update_check", True, type=bool)
        )

        if dialog.exec():
            self.enable_template_detection = dialog.enable_template_detection_checkbox.isChecked()
            self.template_destination = dialog.template_destination_field.text()
            
            output_org_text = dialog.output_org_combo.currentText()
            settings.setValue("output_organization", output_org_text)

            settings.setValue("enable_template_detection", self.enable_template_detection)
            settings.setValue("template_destination", self.template_destination)

            auto_enabled = dialog.auto_update_checkbox.isChecked()
            settings.setValue("auto_update_check", auto_enabled)
            self.updater.set_auto_enabled(auto_enabled)

            (self.storeitems, self.store_prefixes, self.available_tags,
             self.daz_folders) = load_configurations(self.doc_main_dir)
            self.store_input.clear()
            self.store_input.addItems(self.storeitems)
            self.store_completer = QCompleter(self.storeitems, self)
            self.store_input.setCompleter(self.store_completer)

    def toggleAlwaysOnTop(self):
        self.setWindowFlags(self.windowFlags() ^ Qt.WindowType.WindowStaysOnTopHint)
        self.always_on_top_button.setIcon(FIF.UNPIN if self.always_on_top_button.isChecked() else FIF.PIN)
        self.show()

    def generateGUID(self):
        new_guid = str(uuid.uuid4())
        self.guid_input.setText(new_guid)

    def clearAll(self):
        if getattr(self, "packaging_worker", None) and self.packaging_worker.isRunning():
            show_info(self, "Busy", "Cannot clear while packaging is running.")
            return
        if getattr(self, "extractionWorker", None) and self.extractionWorker.isRunning():
            show_info(self, "Busy", "Cannot clear while extraction is running.")
            return
        reply = QMessageBox.question(
            self,
            "Clear Confirmation",
            "Are you sure you want to clear all fields and clean the current build folder?\n"
            "This will delete all files including Manifest.dsx and Supplement.dsx.\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.clearFields()
            self.cleanCurrentBuildFolder()

    def cleanCurrentBuildFolder(self):
        if not self.current_build:
            log.warning("No current build to clean")
            return
        
        build_path = get_build_dir(self.current_build.folder)
        content_dir = get_build_content_dir(self.current_build.folder)
        
        if not os.path.exists(build_path):
            log.warning(f"Build directory does not exist: {build_path}")
            return
        
        log.info(f"Attempting to clean the entire build folder: {build_path}")
        
        for filename in os.listdir(build_path):
            file_path = os.path.join(build_path, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path, onerror=self.handle_remove_readonly)
            except Exception as e:
                log.error(f"Failed to delete {file_path}: {e}")
        
        os.makedirs(content_dir, exist_ok=True)
        
        log.info(f"Build folder successfully cleared: {build_path}")
        
        if hasattr(self, 'fileExplorer'):
            self.fileExplorer.reset_model()
        
        if self.current_build:
            self.current_build.content_status = "empty"
            self.saveSession()
        
        if hasattr(self, 'buildListWidget') and self.buildListWidget:
            try:
                self.buildListWidget.refreshList()
            except Exception as e:
                log.error(f"Failed to refresh build list after cleaning content: {e}")
        
        if hasattr(self, 'fileExplorer'):
            self.fileExplorer.setRootPath(content_dir)


    def handle_remove_readonly(self, func, path, exc_info):
        try:
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
        except Exception:
            pass

    def clearFields(self):
        log.info("Attempting to clear all data.")
        try:
            self.product_name_input.clear()
            self.sku_input.clear()
            self.product_part_input.setValue(1)
            self.generateGUID()
            self.support_clean_input.setChecked(True)
            self.cleanUpTemporaryImage()
            self.image_label.loadPlaceholderImage()
            self.updateZipPreview()
            log.info("All data successfully cleared.")
            show_info(self, "Clearing Successful", "All data successfully cleared.")
        except Exception as e:
            log.error(f"Failed to clear all data: {e}")
            show_error(self, "Error", "Failed to clear all data. Please check the logs for more details.")

    def contentValidation(self, content_dir):
        valid = any(os.path.exists(os.path.join(content_dir, folder)) for folder in self.daz_folders)
        return valid

    def process(self):
        if getattr(self, "packaging_worker", None) and self.packaging_worker.isRunning():
            show_info(self, "Already running", "Packaging is already in progress.")
            return

        store = self.store_input.currentText()
        product_name = self.product_name_input.text()
        prefix = self.prefix_input.text()
        sku = self.sku_input.text()
        product_part = self.product_part_input.value()
        product_tags = self.product_tags_input.text()
        image_path = self.image_label.imagePath
        support_clean = self.support_clean_input.isChecked()
        guid = self.guid_input.text()
        if not guid:
            guid = str(uuid.uuid4())
            self.guid_input.setText(guid)

        if not all([store, product_name, prefix, sku, product_part]):
            show_info(
                self, "Missing Required Fields",
                "Please fill in all required fields to proceed with DIM "
                "package creation.",
                Qt.Vertical
            )
            return

        destination_folder = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder", self.last_destination_folder
        )
        if not destination_folder:
            show_info(
                self, "DIM Creation Canceled",
                "No destination folder selected. DIM package creation has "
                "been canceled.",
                Qt.Vertical
            )
            return
        else:
            self.last_destination_folder = destination_folder
            settings.setValue("last_destination_folder", self.last_destination_folder)
            try:
                settings.sync()
            except Exception:
                pass

        current_content_dir = get_build_content_dir(self.current_build.folder)
        if not self.contentValidation(current_content_dir):
            reply = QMessageBox.question(
                self,
                "Content Validation Failed",
                "No recognized DAZ main folders found in the current build's content "
                "directory. "
                "Do you want to continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                show_info(
                    self, "DIM Creation Canceled",
                    "DIM package creation canceled due to content "
                    "validation failure.",
                    Qt.Vertical
                )
                return

        current_content_dir = get_build_content_dir(self.current_build.folder)
        spec = PackageSpec(
            content_dir=current_content_dir,
            store=store,
            product_name=product_name,
            prefix=prefix,
            sku=sku,
            product_part=product_part,
            product_tags=product_tags,
            image_path=image_path,
            clean_support=support_clean,
            guid=guid,
            destination_folder=destination_folder
        )

        self.packaging_worker = PackagingWorker(spec, parent=self)

        pw = self.packaging_worker
        self.package_all_button.setEnabled(False)
        self.package_selected_button.setEnabled(False)
        self.extract_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self._setImageBusy(True, "Preparing…", 0)

        pw.progress.connect(self.updateProgress)
        pw.finished.connect(self.onPackagingFinished)
        pw.start()

    def updateProgress(self, percent: int, message: str):
        self.progress_ring.setValue(percent)
        self._setImageBusy(True, f"{message}… {percent}%", percent)

    def onPackagingFinished(self, success: bool, message: str):
        if success:
            log.info("Packaging process completed successfully.")
            self.DIMSuccessfullCreatedInfoBar()
        else:
            log.error(f"Packaging process failed: {message}")
            show_error(
                self, "Packaging Error",
                f"An error occurred:<br><small>{message}</small>",
                Qt.Horizontal, InfoBarPosition.TOP_RIGHT, True, 5000
            )

        self.resetPackagingState()

    def resetPackagingState(self):
        try:
            self._setImageBusy(False)
        except Exception:
            pass
        self.package_all_button.setEnabled(True)
        self.package_selected_button.setEnabled(self._hasCheckedBuilds())
        self.extract_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        if getattr(self, 'packaging_worker', None):
            self.packaging_worker.deleteLater()
            self.packaging_worker = None
        if getattr(self, 'batch_packaging_worker', None):
            self.batch_packaging_worker.deleteLater()
            self.batch_packaging_worker = None

    def _hasCheckedBuilds(self):
        if not hasattr(self, 'buildListWidget') or not self.session:
            return False
        
        checked_builds = self.buildListWidget.getCheckedBuilds()
        return len(checked_builds) > 0
    
    def onBuildCheckedChanged(self, build_id: str, checked: bool):
        self.package_selected_button.setEnabled(self._hasCheckedBuilds())
        
        self.saveSession()
    
    def packageAllBuilds(self):
        if not self.session or not self.session.builds:
            show_info(self, "No Builds", "There are no builds to package.")
            return
        
        self._packageBuilds(self.session.builds)
    
    def packageSelectedBuilds(self):
        if not hasattr(self, 'buildListWidget'):
            return
        
        checked_builds = self.buildListWidget.getCheckedBuilds()
        
        if not checked_builds:
            show_info(self, "No Selection", "Please check at least one build to package.")
            return
        
        self._packageBuilds(checked_builds)
    
    def _packageBuilds(self, builds):
        builds_validation = self._validateBuildsForPackaging(builds)
        
        validation_dialog = ValidationDialog(builds_validation, self.session, self)
        dialog_result = validation_dialog.exec()
        
        result = validation_dialog.getResult()
        
        log.info(f"Validation dialog result: {result} (QDialog result: {dialog_result})")
        
        if result == ValidationDialog.RESULT_CANCEL:
            log.info("User cancelled packaging from validation dialog")
            return
        
        builds_to_package = []
        if result == ValidationDialog.RESULT_PACKAGE_ALL:
            log.info("User selected: Package All")
            builds_to_package = builds
        elif result == ValidationDialog.RESULT_PACKAGE_VALID:
            log.info("User selected: Package Valid Only")
            builds_to_package = [
                b['build'] for b in builds_validation 
                if b['status'] == 'ready'
            ]
            ready_count = len(builds_to_package)
            skipped_count = len(builds) - ready_count
            log.info(f"Package Valid Only: packaging {ready_count} ready builds, skipping {skipped_count} incomplete/empty builds")
        
        if not builds_to_package:
            show_info(self, "No Valid Builds", "There are no valid builds to package.")
            return
        
        destination_folder = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder",
            self.last_destination_folder or os.path.expanduser("~")
        )
        
        if not destination_folder:
            log.info("User cancelled destination folder selection")
            return
        
        self.last_destination_folder = destination_folder
        settings.setValue("last_destination_folder", self.last_destination_folder)
        
        output_org = settings.value("output_organization", "Flat", type=str)
        if output_org == "By Date":
            date_str = date.today().strftime("%Y-%m-%d")
            destination_folder = os.path.join(destination_folder, date_str)
            try:
                os.makedirs(destination_folder, exist_ok=True)
                log.info(f"Using by-date organization: {destination_folder}")
            except OSError as e:
                log.error(f"Failed to create date subfolder: {e}")
                show_error(
                    self,
                    "Error Creating Folder",
                    f"Could not create destination folder:\n{destination_folder}\n\nError: {e}"
                )
                self.package_all_button.setEnabled(True)
                self.package_selected_button.setEnabled(self._hasCheckedBuilds())
                self.extract_button.setEnabled(True)
                self.clear_button.setEnabled(True)
                return
        
        build_specs = []
        for build in builds_to_package:
            build_data = get_build_data(self.session, build)
            content_dir = get_build_content_dir(build.folder)
            
            spec = PackageSpec(
                content_dir=content_dir,
                store=build_data.get('store', ''),
                product_name=build_data.get('product_name', ''),
                prefix=build_data.get('prefix', ''),
                sku=build_data.get('sku', ''),
                product_part=build.part,
                product_tags=build_data.get('tags', 'DAZStudio4_5'),
                image_path=build_data.get('image_path', ''),
                clean_support=self.clean_support_checkbox.isChecked() if hasattr(self, 'clean_support_checkbox') else False,
                guid=build.guid,
                destination_folder=destination_folder
            )
            build_specs.append((build, spec))
        
        progress_dialog = BatchProgressDialog(len(builds_to_package), self)
        
        for i, build in enumerate(builds_to_package):
            part_label = f"Build {build.part:02d}"
            build_data = get_build_data(self.session, build)
            product_name = build_data.get('product_name', '') or "(No name)"
            progress_dialog.addBuildStatus('⏳', f"{part_label} - {product_name}")
        
        self.batch_packaging_worker = BatchPackagingWorker(build_specs, self.session, parent=self)
        
        self.package_all_button.setEnabled(False)
        self.package_selected_button.setEnabled(False)
        self.extract_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        
        batch_start_time = time.time()
        
        self.batch_packaging_worker.overallProgress.connect(
            lambda current, total: progress_dialog.updateOverallProgress(current, total)
        )
        self.batch_packaging_worker.buildStarted.connect(
            lambda index, part, name: (
                progress_dialog.startBuild(index, part, name),
                progress_dialog.updateBuildStatus(index, '🔄', f"{part} - {name}")
            )
        )
        self.batch_packaging_worker.buildProgress.connect(
            lambda percent, stage: progress_dialog.updateBuildProgress(percent, stage)
        )
        self.batch_packaging_worker.buildCompleted.connect(
            lambda index, success, message, file_size, output_path: (
                self._onBuildPackaged(progress_dialog, index, success, message, file_size, output_path, builds_to_package)
            )
        )
        self.batch_packaging_worker.allCompleted.connect(
            lambda summary: (
                progress_dialog.accept(),
                self._showBatchResults(summary, destination_folder, time.time() - batch_start_time)
            )
        )
        self.batch_packaging_worker.cancelled.connect(
            lambda: log.info("Batch packaging cancelled by user")
        )
        
        progress_dialog.cancelButton2.clicked.connect(
            lambda: self.batch_packaging_worker.requestCancellation()
        )
        
        self.batch_packaging_worker.start()
        
        progress_dialog.exec()
        
        if self.batch_packaging_worker and self.batch_packaging_worker.isRunning():
            log.warning("Batch packaging worker still running after dialog closed; waiting for completion...")
            self.batch_packaging_worker.wait()
            log.info("Batch packaging worker finished")
        
        self.package_all_button.setEnabled(True)
        self.package_selected_button.setEnabled(self._hasCheckedBuilds())
        self.extract_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        
        self.saveSession()
        
        if self.batch_packaging_worker is not None:
            self.batch_packaging_worker.deleteLater()
            self.batch_packaging_worker = None
    
    def _validateBuildsForPackaging(self, builds):
        validation_results = []
        
        for build in builds:
            issues = []
            
            build_data = get_build_data(self.session, build)
            
            if not build_data.get('store'):
                issues.append("Missing Store")
            if not build_data.get('product_name'):
                issues.append("Missing Product Name")
            if not build_data.get('sku'):
                issues.append("Missing SKU")
            if not build_data.get('prefix'):
                issues.append("Missing Prefix")
            
            try:
                uuid.UUID(build.guid)
            except (ValueError, AttributeError):
                issues.append("Invalid GUID format")
            
            content_dir = get_build_content_dir(build.folder)
            content_has_files = False
            if os.path.exists(content_dir):
                try:
                    entries = os.listdir(content_dir)
                    visible_entries = [
                        name for name in entries
                        if not name.startswith('.') and name not in IGNORE_SYSTEM_FILES
                    ]
                    content_has_files = bool(visible_entries)
                except OSError:
                    # If content directory cannot be listed (permissions, I/O error), treat as having no files
                    pass
            
            if not content_has_files:
                issues.append("Content folder is empty")
            
            if not content_has_files:
                status = 'empty'
            elif issues:
                status = 'incomplete'
            else:
                status = 'ready'
            
            validation_results.append({
                'build': build,
                'status': status,
                'issues': issues
            })
        
        return validation_results
    
    def _onBuildPackaged(self, progress_dialog, index, success, message, file_size, output_path, builds):
        build = builds[index]
        part_label = f"Build {build.part:02d}"
        
        build_data = get_build_data(self.session, build)
        product_name = build_data.get('product_name', '') or "(No name)"
        
        if success:
            size_str = "Unknown" if file_size < 0 else format_file_size(file_size)
            
            status_text = f"{part_label} - {product_name} ({size_str})"
            progress_dialog.updateBuildStatus(index, '✅', status_text)
        else:
            status_text = f"{part_label} - {product_name} (Failed: {message})"
            progress_dialog.updateBuildStatus(index, '❌', status_text)
    
    def _showBatchResults(self, summary, destination_folder, total_time):
        result_dialog = ResultSummaryDialog(
            summary['results'],
            destination_folder,
            total_time,
            self.session,
            self
        )
        result_dialog.exec()
        
        log.info(
            f"Batch packaging complete: {summary['successful']} successful, "
            f"{summary['failed']} failed, {summary['skipped']} skipped"
        )

    def DIMSuccessfullCreatedInfoBar(self):
        show_success(self, "Success", "The DIM has been successfully created and saved.")

    def extractArchive(self):
        if getattr(self, "extractionWorker", None) and self.extractionWorker.isRunning():
            show_info(self, "Extraction running", "Please wait for the current extraction to finish.")
            return

        self._extractionHadError = False
        archive_file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Archive File", "", "Archive Files (*.zip *.rar *.7z)"
        )
        if not archive_file_path:
            return

        self._processArchiveExtraction(archive_file_path)

    def dropExtractArchive(self, archive_file_path):
        if getattr(self, "extractionWorker", None) and self.extractionWorker.isRunning():
            show_info(self, "Extraction running", "Please wait for the current extraction to finish.")
            return

        self._extractionHadError = False
        log.info("Extraction started from TreeView...")

        self._processArchiveExtraction(archive_file_path)
    
    def _processArchiveExtraction(self, archive_file_path):
        try:
            scan_temp_dir = tempfile.mkdtemp(prefix='dim_archive_scan_')
            
            try:
                # Extract once here just to scan for embedded archives;
                # may be re-extracted later during the actual workflow.
                patoolib.extract_archive(archive_file_path, outdir=scan_temp_dir)
                log.info(f"Archive scanned: {archive_file_path}")
                
                embedded_archives = []
                for root, _, files in os.walk(scan_temp_dir):
                    for fname in files:
                        if fname.lower().endswith(('.zip', '.rar', '.7z')):
                            fpath = os.path.join(root, fname)
                            embedded_archives.append(fpath)
                
                persistent_temp_dir = None
                try:
                    if embedded_archives:
                        persistent_temp_dir = tempfile.mkdtemp(prefix='dim_embedded_')
                        self._extraction_temp_dir = persistent_temp_dir
                        persistent_archives = []
                        
                        for archive_path in embedded_archives:
                            dest_path = os.path.join(persistent_temp_dir, os.path.basename(archive_path))
                            shutil.copy2(archive_path, dest_path)
                            persistent_archives.append(dest_path)
                        
                        embedded_archives = persistent_archives
                    
                    shutil.rmtree(scan_temp_dir, ignore_errors=True)
                    
                    content_archives, template_archives, ignored_archives = classify_archives(
                        embedded_archives, self.enable_template_detection
                    )
                    
                    if content_archives:
                        content_archives, warning = detect_heuristic_ordering(content_archives)
                    else:
                        warning = None
                except Exception:
                    if persistent_temp_dir is not None and os.path.exists(persistent_temp_dir):
                        shutil.rmtree(persistent_temp_dir, ignore_errors=True)
                        if hasattr(self, '_extraction_temp_dir') and self._extraction_temp_dir == persistent_temp_dir:
                            self._extraction_temp_dir = None
                    raise
                
                should_show_dialog = (
                    len(content_archives) > 1 or
                    len(template_archives) > 0 or
                    len(ignored_archives) > 0
                )
                
                if not should_show_dialog and len(content_archives) == 1:
                    # Re-extracts the archive (worker expects a file, not pre-extracted content).
                    log.info("Single content archive detected, extracting directly...")
                    if persistent_temp_dir is not None and os.path.exists(persistent_temp_dir):
                        shutil.rmtree(persistent_temp_dir, ignore_errors=True)
                        if hasattr(self, '_extraction_temp_dir') and self._extraction_temp_dir == persistent_temp_dir:
                            self._extraction_temp_dir = None
                    self._extractDirectly(archive_file_path)
                elif len(content_archives) == 0 and len(embedded_archives) == 0:
                    log.info("No embedded archives detected, extracting directly...")
                    if persistent_temp_dir is not None and os.path.exists(persistent_temp_dir):
                        shutil.rmtree(persistent_temp_dir, ignore_errors=True)
                        if hasattr(self, '_extraction_temp_dir') and self._extraction_temp_dir == persistent_temp_dir:
                            self._extraction_temp_dir = None
                    self._extractDirectly(archive_file_path)
                else:
                    log.info("Multiple archives or templates/ignored detected, showing dialog...")
                    self._showExtractionDialog(
                        content_archives, template_archives, ignored_archives, warning
                    )
                    
            except Exception as e:
                if scan_temp_dir and os.path.isdir(scan_temp_dir):
                    shutil.rmtree(scan_temp_dir, ignore_errors=True)
                raise
                
        except Exception as e:
            log.error(f"Failed to analyze archive: {e}")
            self._extractDirectly(archive_file_path)
    
    def _extractDirectly(self, archive_file_path):
        self.showExtractionState(True)
        log.info("Extraction started...")

        current_content_dir = get_build_content_dir(self.current_build.folder)
        w = ContentExtractionWorker(
            archive_file_path,
            set(self.daz_folders),
            current_content_dir,
            self.enable_template_detection,
            self.template_destination,
            parent=self
        )
        self.extractionWorker = w

        w.extractionComplete.connect(self.onExtractionComplete)
        w.extractionError.connect(self.onExtractionError)

        w.finished.connect(self._cleanupExtractionWorker)
        w.finished.connect(w.deleteLater)

        w.start()
    
    def _showExtractionDialog(self, content_archives, template_archives, ignored_archives, warning):
        dialog = ExtractionDialog(
            content_archives,
            template_archives,
            ignored_archives,
            self.enable_template_detection,
            self.session.builds,
            self
        )
        
        if warning:
            dialog.setWarningMessage(warning)
        
        dialog.exec()
        result = dialog.getResult()
        
        if result == ExtractionDialog.RESULT_EXTRACT:
            content_list, template_list, ignored_list = dialog.getArchiveLists()
            
            if not content_list and not template_list:
                show_warning(self, "No Archives Selected", 
                           "No archives selected for extraction.")
                if hasattr(self, '_extraction_temp_dir') and self._extraction_temp_dir:
                    try:
                        if os.path.isdir(self._extraction_temp_dir):
                            shutil.rmtree(self._extraction_temp_dir, ignore_errors=True)
                            log.info("Cleaned up extraction temp directory after no selection")
                    except Exception as e:
                        log.warning(f"Failed to cleanup temp directory: {e}")
                    finally:
                        self._extraction_temp_dir = None
                return
            
            self._startMultiBuildExtraction(content_list, template_list)
        else:
            log.info("Extraction cancelled by user")
            
            if hasattr(self, '_extraction_temp_dir') and self._extraction_temp_dir:
                try:
                    if os.path.isdir(self._extraction_temp_dir):
                        shutil.rmtree(self._extraction_temp_dir, ignore_errors=True)
                        log.info(f"Cleaned up extraction temp directory after cancel")
                except Exception as e:
                    log.warning(f"Failed to cleanup temp directory: {e}")
                finally:
                    self._extraction_temp_dir = None
    
    def _startMultiBuildExtraction(self, content_archives, template_archives):
        self.showExtractionState(True)
        log.info("Starting multi-build extraction...")
        
        w = MultiBuildExtractionWorker(
            content_archives,
            template_archives,
            set(self.daz_folders),
            self.session,
            self.enable_template_detection,
            self.template_destination,
            parent=self
        )
        self.extractionWorker = w
        
        w.extractionComplete.connect(self.onMultiBuildExtractionComplete)
        w.extractionError.connect(self.onExtractionError)
        w.extractionProgress.connect(self.onExtractionProgress)
        
        w.finished.connect(self._cleanupExtractionWorker)
        w.finished.connect(w.deleteLater)
        
        w.start()
    
    def onExtractionProgress(self, message):
        log.info(f"Extraction progress: {message}")
    
    def onMultiBuildExtractionComplete(self, modified_builds):
        if not self._extractionHadError:
            self.showExtractionState(False, "Extraction completed successfully 😆", success=True)
            log.info(f"Multi-build extraction completed. Modified builds: {modified_builds}")
            
            if hasattr(self, '_extraction_temp_dir') and self._extraction_temp_dir:
                try:
                    if os.path.isdir(self._extraction_temp_dir):
                        shutil.rmtree(self._extraction_temp_dir, ignore_errors=True)
                        log.info(f"Cleaned up extraction temp directory: {self._extraction_temp_dir}")
                except Exception as e:
                    log.warning(f"Failed to cleanup temp directory: {e}")
                finally:
                    self._extraction_temp_dir = None
            
            self.saveSession()
            
            self._revalidateAllBuildsStatus()
            
            self.buildListWidget.refreshList()
            
            self.fileExplorer.refresh_view()
            
            worker = self.sender()
            copied = getattr(worker, "copiedTemplates", None)
            if copied:
                for templateName in copied:
                    show_info(
                        self, "Template Copied",
                        f"Template <b>{templateName}</b> copied successfully.",
                        Qt.Vertical, InfoBarPosition.BOTTOM_RIGHT
                    )
            
            if len(modified_builds) > 0:
                show_success(
                    self, "Extraction Complete",
                    f"Successfully extracted to {len(modified_builds)} build(s).",
                    Qt.Vertical, InfoBarPosition.BOTTOM_RIGHT
                )

    def _cleanupExtractionWorker(self):
        w = getattr(self, "extractionWorker", None)
        if not w:
            return
        try:
            if w.isRunning():
                w.requestInterruption()
                w.wait(2000)
        except Exception:
            pass
        self.extractionWorker = None

    def onExtractionComplete(self):
        if not self._extractionHadError:
            self.showExtractionState(False, "Extraction completed successfully 😆", success=True)
            log.info("Extraction Process completed.")
            self.fileExplorer.refresh_view()

            worker = self.sender()
            copied = getattr(worker, "copiedTemplates", None)
            if copied:
                for templateName in copied:
                    show_info(
                        self, "Template Copied",
                        f"Template <b>{templateName}</b> copied successfully.",
                        Qt.Vertical, InfoBarPosition.BOTTOM_RIGHT
                    )

    def onExtractionError(self, message):
        self._extractionHadError = True
        log.error(f"Extraction Error: {message}")
        
        if hasattr(self, '_extraction_temp_dir') and self._extraction_temp_dir:
            try:
                if os.path.isdir(self._extraction_temp_dir):
                    shutil.rmtree(self._extraction_temp_dir, ignore_errors=True)
                    log.info(f"Cleaned up extraction temp directory after error")
            except Exception as e:
                log.warning(f"Failed to cleanup temp directory: {e}")
            finally:
                self._extraction_temp_dir = None
        
        if self.stateTooltip:
            try:
                self.stateTooltip.close()
            except Exception:
                pass
            self.stateTooltip = None
        show_error(
            self, "Extraction failed", message, Qt.Vertical,
            InfoBarPosition.BOTTOM_RIGHT, True, 3000
        )

    def _close_tip(self, tip_attr):
        tip = getattr(self, tip_attr, None)
        if tip:
            try:
                if shiboken6.isValid(tip):
                    tip.close()
            except Exception:
                pass
            setattr(self, tip_attr, None)

    def showExtractionState(self, isExtracting, message=None, success=True):
        if isExtracting:
            self._close_tip("stateTooltip")
            tip = StateToolTip('Extracting', 'Please wait...', self)
            tip_x = self.width() - tip.width() - 30
            tip.move(tip_x, 30)
            tip.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            tip.show()
            self.stateTooltip = tip
            return

        self._close_tip("stateTooltip")
        self._close_tip("_finalTip")

        title = 'Extraction completed' if success else 'Extraction canceled'
        final_tip = StateToolTip(
            title,
            message or ('Done.' if success else 'An error occurred.'),
            self
        )
        final_tip.setState(success)
        final_tip_x = self.width() - final_tip.width() - 30
        final_tip.move(final_tip_x, 30)
        final_tip.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        final_tip.show()

        self._finalTip = final_tip

        def _safe_close(tip=final_tip, self=self):
            if shiboken6.isValid(tip):
                try:
                    tip.close()
                except RuntimeError:
                    pass
            if getattr(self, "_finalTip", None) is tip:
                setattr(self, "_finalTip", None)

        QTimer.singleShot(1800, _safe_close)


if __name__ == '__main__':
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Syst3mApps.DIMCreator")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setOrganizationName("Syst3mApps")
    app.setApplicationName("DIMCreator")
    app.setWindowIcon(QIcon(logo_path))
    ex = DIMPackageGUI()
    ex.show()
    sys.exit(app.exec())
