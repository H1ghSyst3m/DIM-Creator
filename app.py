import sys
import os
import tempfile
import stat
import uuid
import ctypes
import shiboken6
import time
from datetime import date

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from qfluentwidgets import (
    PrimaryPushButton, PushButton, LineEdit, setTheme, Theme,
    EditableComboBox, CheckBox, InfoBarPosition, ToolButton,
    StateToolTip, DropDownPushButton, RoundMenu, Action
)
from qfluentwidgets import FluentIcon as FIF
from PySide6.QtWidgets import (
    QMessageBox, QApplication, QWidget, QLabel, QDialog,
    QVBoxLayout, QFileDialog, QCompleter, QHBoxLayout,
    QSizePolicy, QFormLayout, QSpacerItem
)
from PySide6.QtCore import (
    Qt, QSettings, QTimer, QRegularExpression
)
from PySide6.QtGui import (
    QIcon, QKeySequence, QIntValidator, QRegularExpressionValidator,
    QShortcut
)

from utils import (
    resource_path, DOC_MAIN_DIR,
    tooltip_stylesheet, label_stylesheet,
    show_error, show_info, show_success, show_warning,
    ensure_builds_directory_structure, create_build_folder, clean_build_content,
    get_build_content_dir, get_build_dir,
    SESSION_FILE, delete_all_build_folders,
    IGNORE_SYSTEM_FILES, format_file_size
)
from logger_utils import get_logger
from widgets import (
    ProductLineEdit, TagSelectionDialog, CustomCompactSpinBox, ImageLabel,
    FileExplorer, BuildListWidget
)
from packaging_utils import (
    BatchPackagingWorker, PackageInventory, PackageSpec, PackagingError,
    validate_package_destination, validate_package_spec,
)
from naming_utils import (
    build_dim_zip_filename, validate_dim_part, validate_dim_prefix,
    validate_dim_sku,
)
from extraction_utils import (
    ArchivePlanningResult, ArchivePlanningWorker,
    ConflictPolicy, ContentExtractionWorker, ExtractionResult,
    ExtractionRollbackError,
    MultiBuildExtractionWorker,
)
from config_utils import load_configurations
from settings import SettingsDialog
from updater import UpdateManager
from version import APP_VERSION
from session import (
    MAX_BUILDS, Build, Session, SessionRecoveryError,
    UnsupportedSessionVersionError,
    create_default_session, delete_session_artifacts, load_session_result,
    save_session,
)
from operation_state import OperationState
from build_manager import (
    create_build, delete_build, get_build_data, validate_build,
    set_field_override, sync_to_children, sync_from_parent, get_effective_value
)
from dialogs import ExitDialog, ExtractionDialog, ValidationDialog, BatchProgressDialog, ResultSummaryDialog

log = get_logger(__name__)
log.info("Application starting...")

settings = QSettings("Syst3mApps", "DIMCreator")

logo_path = resource_path(
    os.path.join('assets', 'images', 'logo', 'favicon.ico')
)


def _is_complete_guid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value.casefold()
    except (ValueError, AttributeError, TypeError):
        return False


class DIMPackageGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.operation_state = OperationState.IDLE
        self._close_ready = False
        self._pending_planning_results = 0
        self._pending_extraction_results = 0
        self._pending_rollback_result = None
        self._close_poll_timer = QTimer(self)
        self._close_poll_timer.setSingleShot(True)
        self._close_poll_timer.timeout.connect(self._finishDeferredClose)
        self.doc_main_dir = DOC_MAIN_DIR
        (self.storeitems, self.store_prefixes, self.available_tags,
         self.daz_folders) = load_configurations(self.doc_main_dir)
        self.stateTooltip = None
        
        self.session = None
        self.current_build = None
        self.archivePlanningWorker = None
        self._archive_import_plan = None
        self._loading_build = False
        self._session_warning = ""
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
        if self._session_warning:
            QTimer.singleShot(
                0,
                lambda: show_warning(
                    self, "Session Recovered", self._session_warning,
                    Qt.Vertical, duration=8000,
                ),
            )
        self.updater = UpdateManager(
            self, settings, current_version=APP_VERSION, interval_hours=24
        )
        self.updater.schedule_on_startup_if_enabled()

    def loadSettings(self):
        self.last_destination_folder = settings.value(
            "last_destination_folder", os.path.expanduser("~"), type=str
        )
        self.enable_template_detection = settings.value(
            "enable_template_detection", False, type=bool
        )
        self.template_destination = settings.value(
            "template_destination", "", type=str
        )

        self.use_store_prefix_checkbox.blockSignals(True)
        self.use_store_prefix_checkbox.setChecked(
            settings.value("auto_prefix", False, type=bool)
        )
        self.use_store_prefix_checkbox.blockSignals(False)
        self.prefix_input.setEnabled(
            not self.use_store_prefix_checkbox.isChecked()
        )

    def saveSettings(self):
        store = self.store_input.currentText().strip()
        if store:
            settings.setValue("store_input", store)
        settings.setValue("last_destination_folder", self.last_destination_folder)
        settings.setValue("auto_prefix", self.use_store_prefix_checkbox.isChecked())
        settings.sync()

    def _preferredStoreForNewSession(self) -> str:
        preferred = settings.value("store_input", "", type=str).strip()
        for store in self.storeitems:
            if store.casefold() == preferred.casefold():
                return store
        return self.storeitems[0] if self.storeitems else ""

    def canMutateWorkspace(self) -> bool:
        return self.operation_state is OperationState.IDLE

    def _setOperationState(self, state: OperationState):
        self.operation_state = state
        busy = state is not OperationState.IDLE
        if busy:
            save_timer = getattr(self, "_save_timer", None)
            if save_timer is not None:
                save_timer.stop()
            image_label = getattr(self, "image_label", None)
            abort_download = getattr(image_label, "_abort_active_download", None)
            if callable(abort_download):
                abort_download()
        for name in (
            "buildListWidget", "fileExplorer", "store_input", "prefix_input",
            "product_name_input", "sku_input", "product_tags_input",
            "guid_input", "product_part_input", "image_label",
            "support_clean_input", "use_store_prefix_checkbox", "clear_button",
            "extract_button", "package_all_button", "package_selected_button",
            "settings_button", "generate_guid_button", "sync_container_widget",
            "tags_button",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(not busy)
        if not busy:
            self.package_selected_button.setEnabled(self._hasCheckedBuilds())
            self.prefix_input.setEnabled(
                not self.use_store_prefix_checkbox.isChecked()
            )

    def _runningWorkers(self):
        workers = []
        for name in (
            "batch_packaging_worker", "archivePlanningWorker",
            "extractionWorker",
        ):
            worker = getattr(self, name, None)
            if worker is not None and worker.isRunning():
                workers.append(worker)
        updater_thread = getattr(getattr(self, "updater", None), "_thread", None)
        if updater_thread is not None and updater_thread.isRunning():
            workers.append(updater_thread)
        return workers

    def _requestWorkerCancellation(self):
        for worker in self._runningWorkers():
            cancel = getattr(worker, "requestCancellation", None)
            if callable(cancel):
                cancel()
            worker.requestInterruption()

    def _finishDeferredClose(self):
        pending_rollback = getattr(self, "_pending_rollback_result", None)
        if pending_rollback is not None and pending_rollback.rollback_pending:
            self._close_ready = False
            self._setOperationState(OperationState.EXTRACTING)
            return
        if (
            self._runningWorkers()
            or getattr(self, "_pending_planning_results", 0)
            or getattr(self, "_pending_extraction_results", 0)
        ):
            self._requestWorkerCancellation()
            self._close_poll_timer.start(100)
            return
        self._close_ready = True
        self.close()

    def _isPristineSession(self) -> bool:
        if not self.session or len(self.session.builds) != 1:
            return False

        build = self.session.builds[0]
        for field in ('product_name', 'sku', 'image_path'):
            value = get_effective_value(self.session, build, field)
            if value and value.strip():
                return False

        try:
            content_dir = get_build_content_dir(build.folder)
            if os.path.exists(content_dir):
                ignored = {name.casefold() for name in IGNORE_SYSTEM_FILES}
                entries = (
                    entry for entry in os.listdir(content_dir)
                    if entry.casefold() not in ignored
                )
                if next(entries, None) is not None:
                    return False
        except OSError as exc:
            log.warning(
                "Error checking content directory for %s: %s",
                build.folder,
                exc,
            )
            return False

        return True

    def hasUserMadeChanges(self) -> bool:
        if not self.session or not self.session.builds:
            return False
        return not self._isPristineSession()

    def performSessionCleanup(self) -> tuple[bool, list[str]]:
        try:
            if hasattr(self, '_save_timer') and self._save_timer is not None:
                self._save_timer.stop()
            
            log.info("Cleaning up all builds...")
            failed = delete_all_build_folders(handle_error_callback=self.handle_remove_readonly)
            
            if failed:
                log.warning(f"Some build folders failed to delete: {failed}")
                return (True, failed)

            delete_session_artifacts(SESSION_FILE, include_backups=True)
            
            log.info("Cleanup complete")
            return (True, failed)
        except Exception as e:
            log.error(f"Error during cleanup: {e}")
            return (False, [])
    
    def onNewSession(self):
        if not self.canMutateWorkspace():
            show_info(self, "Busy", "Please wait for the current operation to finish.")
            return
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
            show_error(
                self,
                "Cleanup Incomplete",
                "A new session was not created because these build folders could "
                f"not be removed: {', '.join(failed)}"
            )
            return
        
        if hasattr(self.session, 'builds'):
            self.session.builds.clear()
        
        self.session = create_default_session()
        first_build = self.session.builds[0]
        
        first_build.store = preserved_store.strip() or self._preferredStoreForNewSession()
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
        pending_rollback = getattr(self, "_pending_rollback_result", None)
        if pending_rollback is not None and pending_rollback.rollback_pending:
            rollback_error = self._retryExtractionRollback(pending_rollback)
            if rollback_error is not None:
                self._close_ready = False
                self._setOperationState(OperationState.EXTRACTING)
                event.ignore()
                return
        if (
            not self._close_ready
            and (
                self._runningWorkers()
                or getattr(self, "_pending_planning_results", 0)
                or getattr(self, "_pending_extraction_results", 0)
            )
        ):
            self._setOperationState(OperationState.CLOSING)
            self._requestWorkerCancellation()
            self._close_poll_timer.start(100)
            event.ignore()
            return

        show_dialog = self.hasUserMadeChanges()
        result = ExitDialog.RESULT_SAVE
        
        if show_dialog:
            dialog = ExitDialog(self)
            dialog.exec()
            result = dialog.getResult()
            
            if result == ExitDialog.RESULT_CANCEL:
                self._close_ready = False
                self._setOperationState(OperationState.IDLE)
                event.ignore()
                return
            elif result == ExitDialog.RESULT_CLEAN:
                success, failed = self.performSessionCleanup()
                
                if not success:
                    show_error(self, "Cleanup Error", "Failed to clean up session")
                    self._close_ready = False
                    self._setOperationState(OperationState.IDLE)
                    event.ignore()
                    return
                elif failed:
                    show_error(
                        self,
                        "Cleanup Incomplete",
                        "The app remains open because these build folders could not "
                        f"be removed: {', '.join(failed)}"
                    )
                    self._close_ready = False
                    self._setOperationState(OperationState.IDLE)
                    event.ignore()
                    return

        self._setOperationState(OperationState.CLOSING)

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

        try:
            self.saveSettings()
        except Exception as exc:
            log.warning("Could not save UI settings during shutdown: %s", exc)

        should_save = not show_dialog or result == ExitDialog.RESULT_SAVE
        if should_save and not self.saveSession():
            self._close_ready = False
            self._setOperationState(OperationState.IDLE)
            event.ignore()
            return

        super().closeEvent(event)

    def ensure_directory_structure(self):
        ensure_builds_directory_structure()
    
    def loadSession(self):
        try:
            result = load_session_result(SESSION_FILE)
        except UnsupportedSessionVersionError as exc:
            QMessageBox.critical(
                self, "Session Version Not Supported",
                f"{exc}\n\nThe session was not changed. Install a newer "
                "DIM-Creator version to open it.",
            )
            raise RuntimeError(str(exc)) from exc
        except SessionRecoveryError as exc:
            reply = QMessageBox.warning(
                self, "Session Recovery Failed",
                f"{exc}\n\nCreate a new session? The quarantined session will "
                "be kept for manual recovery.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                raise RuntimeError(str(exc)) from exc
            result = None

        self.session = result.session if result is not None else None
        if result is not None and result.warning:
            self._session_warning = result.warning

        if self.session is None:
            log.info("No session found, creating new session with Build 1")
            self.session = create_default_session()
            self.session.builds[0].store = self._preferredStoreForNewSession()
            
            create_build_folder(self.session.builds[0].folder)
            
            if not self.saveSession():
                raise RuntimeError("Could not create the initial session file")
        else:
            log.info(f"Session loaded with {len(self.session.builds)} builds")
            
            for build in self.session.builds:
                content_dir = get_build_content_dir(build.folder)
                if not os.path.exists(content_dir):
                    log.warning(f"Build folder missing: {build.folder}, recreating")
                    create_build_folder(build.folder)

            if self._isPristineSession():
                self.session.builds[0].guid = str(uuid.uuid4())

            self._revalidateAllBuildsStatus()
        
        if self.session.builds:
            self.current_build = next(
                (
                    build for build in self.session.builds
                    if build.id == self.session.last_selected_build_id
                ),
                self.session.builds[0],
            )
    
    def saveSession(self):
        if not self.session:
            return True
        try:
            save_session(self.session, SESSION_FILE)
            log.info("Session saved successfully")
            return True
        except Exception as e:
            log.error(f"Failed to save session: {e}")
            show_error(self, "Session Save Error", f"Failed to save session: {e}")
            return False
    
    def onBuildSelected(self, build_id: str):
        if not self.canMutateWorkspace():
            return
        for build in self.session.builds:
            if build.id == build_id:
                self.current_build = build
                self.session.last_selected_build_id = build.id
                
                self.loadBuildIntoEditor(build)
                
                content_dir = get_build_content_dir(build.folder)
                if hasattr(self, 'fileExplorer'):
                    self.fileExplorer.setRootPath(content_dir)
                
                self.saveSession()
                break
    
    def onBuildAdded(self):
        if not self.canMutateWorkspace():
            return
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
        if not self.canMutateWorkspace():
            return
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
        if not self.canMutateWorkspace():
            return
        if self.current_build:
            selected = next(
                (
                    build for build in self.session.builds
                    if build.id == self.current_build.id
                ),
                None,
            )
            if selected is not None:
                self.current_build = selected
                self.session.last_selected_build_id = selected.id
                self.loadBuildIntoEditor(selected)
            elif self.session.builds:
                log.warning(f"Current build {self.current_build.id} not found after reorder, selecting first build")
                self.current_build = self.session.builds[0]
                self.session.last_selected_build_id = self.current_build.id
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
                managed_path = self.image_label.imagePath
                if managed_path and managed_path != image_path:
                    source_build = build
                    if build.part > 1 and 'image_path' not in build.overrides:
                        source_build = next(
                            (item for item in self.session.builds if item.part == 1),
                            build,
                        )
                    set_field_override(
                        self.session, source_build, 'image_path', managed_path
                    )
                    self._save_timer.start()
            elif hasattr(self, 'image_label'):
                self.image_label.resetToPlaceholder(emit=False)
            
            self.updateZipPreview()
            
            self.updateSyncControlsVisibility()
        finally:
            self._loading_build = False
    
    def saveBuildFieldChanges(self):
        if self._loading_build:
            return
        if not self.canMutateWorkspace():
            return
        if not self.current_build or not self.session:
            return
        
        set_field_override(self.session, self.current_build, 'store', self.store_input.currentText())
        set_field_override(self.session, self.current_build, 'product_name', self.product_name_input.text())
        set_field_override(self.session, self.current_build, 'prefix', self.prefix_input.text())
        set_field_override(self.session, self.current_build, 'sku', self.sku_input.text())
        set_field_override(self.session, self.current_build, 'tags', self.product_tags_input.text())
        
        if hasattr(self, 'image_label'):
            image_path = self.image_label.imagePath
            set_field_override(
                self.session, self.current_build, 'image_path', image_path or ""
            )
        
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
        if not self.canMutateWorkspace():
            return
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
        if not self.canMutateWorkspace():
            return
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
    
    def onGuidChanged(self, guid: str):
        if self._loading_build:
            return
        if not self.canMutateWorkspace():
            return
        if not self.current_build or not self.session:
            return
        if not _is_complete_guid(guid):
            return
        self.current_build.guid = guid
        self.saveBuildFieldChanges()
    
    def onImageChanged(self, image_path):
        if self._loading_build:
            return
        if not self.canMutateWorkspace():
            return
        if self.current_build and self.session:
            self.saveBuildFieldChanges()

    def openTagSelectionDialog(self):
        if not self.canMutateWorkspace():
            return
        selected_tags = self.product_tags_input.text().split(",")

        dialog = TagSelectionDialog(self.available_tags, selected_tags, self)
        if dialog.exec() == QDialog.Accepted:
            selected_tags = dialog.getSelectedTags()
            self.product_tags_input.setText(",".join(selected_tags))

    def updateSourcePrefixBasedOnStore(self):
        if not self.canMutateWorkspace():
            self.prefix_input.setEnabled(False)
            return
        use_store_prefix = self.use_store_prefix_checkbox.isChecked()
        self.prefix_input.setEnabled(not use_store_prefix)

        if use_store_prefix:
            selected_store = self.store_input.currentText()
            store_prefix = self.store_prefixes.get(selected_store, "")
            self.prefix_input.setText(store_prefix)

        self.updateZipPreview()

    def build_zip_filename(self) -> str:
        prefix_raw = self.prefix_input.text() or "LOCAL"
        sku_raw = self.sku_input.text() or ""
        part_val = self.product_part_input.value()
        name_raw = self.product_name_input.text() or "Package"

        return build_dim_zip_filename(prefix_raw, sku_raw, part_val, name_raw)

    def updateZipPreview(self):
        try:
            if hasattr(self, 'zip_preview_edit'):
                self.zip_preview_edit.setText(self.build_zip_filename())
                self.zip_preview_edit.setCursorPosition(0)
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
        self.prefix_input.setPlaceholderText("LOCAL")
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

        self.image_label = ImageLabel(right_wrap)
        self.image_label.setToolTip("Drop an image here or click to select an image file.")
        self.image_label.setMinimumSize(300, 320)
        self.image_label.setMaximumWidth(400)
        right.addWidget(self.image_label, 1)
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
        QShortcut(QKeySequence("Ctrl+Enter"), self, self.process)
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
        if not self.canMutateWorkspace():
            return
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

            self._reloadConfigurationChoices()

    def _reloadConfigurationChoices(self):
        selected_store = self.store_input.currentText()
        (self.storeitems, self.store_prefixes, self.available_tags,
         self.daz_folders) = load_configurations(self.doc_main_dir)
        signals_were_blocked = self.store_input.blockSignals(True)
        try:
            self.store_input.clear()
            self.store_input.addItems(self.storeitems)
            index = self.store_input.findText(selected_store)
            if index >= 0:
                self.store_input.setCurrentIndex(index)
            else:
                self.store_input.setCurrentIndex(-1)
                self.store_input.setCurrentText(selected_store)
        finally:
            self.store_input.blockSignals(signals_were_blocked)
        self.store_completer = QCompleter(self.storeitems, self)
        self.store_input.setCompleter(self.store_completer)

    def toggleAlwaysOnTop(self):
        self.setWindowFlags(self.windowFlags() ^ Qt.WindowType.WindowStaysOnTopHint)
        self.always_on_top_button.setIcon(FIF.UNPIN if self.always_on_top_button.isChecked() else FIF.PIN)
        self.show()

    def generateGUID(self):
        if not self.canMutateWorkspace():
            return
        new_guid = str(uuid.uuid4())
        self.guid_input.setText(new_guid)

    def clearAll(self):
        if not self.canMutateWorkspace():
            show_info(self, "Busy", "Please wait for the current operation to finish.")
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
            if self.cleanCurrentBuildFolder():
                self.clearFields()

    def cleanCurrentBuildFolder(self):
        if not self.canMutateWorkspace():
            return False
        if not self.current_build:
            log.warning("No current build to clean")
            return False
        
        build_path = get_build_dir(self.current_build.folder)
        content_dir = get_build_content_dir(self.current_build.folder)
        
        if not os.path.exists(build_path):
            log.warning(f"Build directory does not exist: {build_path}")
            return False
        
        try:
            clean_build_content(self.current_build.folder)
        except OSError as exc:
            log.error("Failed to clean build folder %s: %s", build_path, exc)
            show_error(
                self, "Clean Failed",
                "The build was not marked empty because cleanup was incomplete:<br>"
                + str(exc),
            )
            self._revalidateAllBuildsStatus()
            return False

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
        return True


    def handle_remove_readonly(self, func, path, exc_info):
        if not path:
            raise OSError("Cleanup callback received an empty path")
        os.chmod(path, stat.S_IWRITE)
        func(path)

    def clearFields(self):
        log.info("Attempting to clear all data.")
        try:
            self.product_name_input.clear()
            self.sku_input.clear()
            self.product_part_input.setValue(1)
            self.generateGUID()
            self.support_clean_input.setChecked(True)
            self.image_label.resetToPlaceholder()
            self.updateZipPreview()
            log.info("All data successfully cleared.")
            show_info(self, "Clearing Successful", "All data successfully cleared.")
        except Exception as e:
            log.error(f"Failed to clear all data: {e}")
            show_error(self, "Error", "Failed to clear all data. Please check the logs for more details.")

    def process(self):
        if not self.current_build:
            show_info(self, "No Build", "There is no build to package.")
            return
        self._packageBuilds([self.current_build])

    def _hasCheckedBuilds(self):
        if not hasattr(self, 'buildListWidget') or not self.session:
            return False
        
        checked_builds = self.buildListWidget.getCheckedBuilds()
        return len(checked_builds) > 0
    
    def onBuildCheckedChanged(self, build_id: str, checked: bool):
        if not self.canMutateWorkspace():
            return
        self.package_selected_button.setEnabled(self._hasCheckedBuilds())
        
        self.saveSession()
    
    def packageAllBuilds(self):
        if not self.canMutateWorkspace():
            return
        if not self.session or not self.session.builds:
            show_info(self, "No Builds", "There are no builds to package.")
            return
        
        self._packageBuilds(self.session.builds)
    
    def packageSelectedBuilds(self):
        if not self.canMutateWorkspace():
            return
        if not hasattr(self, 'buildListWidget'):
            return
        
        checked_builds = self.buildListWidget.getCheckedBuilds()
        
        if not checked_builds:
            show_info(self, "No Selection", "Please check at least one build to package.")
            return
        
        self._packageBuilds(checked_builds)
    
    def _packageBuilds(self, builds):
        if not self.canMutateWorkspace():
            show_info(self, "Busy", "Please wait for the current operation to finish.")
            return
        builds_validation = self._validateBuildsForPackaging(builds)
        validation_dialog = ValidationDialog(builds_validation, self.session, self)
        dialog_result = validation_dialog.exec()
        result = validation_dialog.getResult()
        log.info(f"Validation dialog result: {result} (QDialog result: {dialog_result})")

        if result == ValidationDialog.RESULT_CANCEL:
            return

        if result == ValidationDialog.RESULT_PACKAGE_ALL:
            builds_to_package = list(builds)
        elif result == ValidationDialog.RESULT_PACKAGE_VALID:
            builds_to_package = [
                b['build'] for b in builds_validation
                if b['status'] == 'ready'
            ]
        else:
            return

        if not builds_to_package:
            show_info(self, "No Valid Builds", "There are no valid builds to package.")
            return

        destination_folder = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder",
            self.last_destination_folder or os.path.expanduser("~")
        )
        if not destination_folder:
            return

        self.last_destination_folder = destination_folder
        settings.setValue("last_destination_folder", self.last_destination_folder)
        output_org = settings.value("output_organization", "Flat", type=str)
        if output_org == "By Date":
            destination_errors = []
            for build in builds_to_package:
                try:
                    validate_package_destination(
                        get_build_content_dir(build.folder), destination_folder
                    )
                except (OSError, PackagingError, ValueError) as exc:
                    destination_errors.append(f"Build {build.part:02d}: {exc}")
            if destination_errors:
                show_error(
                    self,
                    "Packaging Validation Failed",
                    "<br>".join(destination_errors),
                    Qt.Vertical,
                    InfoBarPosition.TOP_RIGHT,
                    True,
                    10000,
                )
                return
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
                return

        build_specs = []
        output_paths = []
        preflight_errors = []
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
                clean_support=self.support_clean_input.isChecked(),
                guid=build.guid,
                destination_folder=destination_folder,
                recognized_content_roots=tuple(self.daz_folders),
            )
            build_specs.append((build, spec))
            try:
                output_paths.append(validate_package_spec(spec))
            except (OSError, PackagingError, ValueError) as exc:
                preflight_errors.append(f"Build {build.part:02d}: {exc}")

        folded_outputs = [os.path.normcase(os.path.abspath(path)) for path in output_paths]
        if len(folded_outputs) != len(set(folded_outputs)):
            preflight_errors.append("Multiple builds would create the same output file.")
        if preflight_errors:
            show_error(
                self, "Packaging Validation Failed",
                "<br>".join(preflight_errors), Qt.Vertical,
                InfoBarPosition.TOP_RIGHT, True, 10000,
            )
            return

        try:
            with tempfile.NamedTemporaryFile(
                    prefix=".dimcreator-write-test-", dir=destination_folder,
                    delete=True):
                pass
        except OSError as exc:
            show_error(self, "Destination Not Writable", str(exc))
            return

        existing_outputs = [path for path in output_paths if os.path.exists(path)]
        if existing_outputs:
            names = "\n".join(f"• {os.path.basename(path)}" for path in existing_outputs)
            replace = QMessageBox.question(
                self, "Replace Existing Packages?",
                f"The following packages already exist:\n\n{names}\n\nReplace them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if replace != QMessageBox.StandardButton.Yes:
                return

        approved_replacements = {
            os.path.abspath(path).casefold() for path in existing_outputs
        }
        for (_, spec), output_path in zip(build_specs, output_paths):
            spec.replace_existing = (
                os.path.abspath(output_path).casefold() in approved_replacements
            )

        progress_dialog = BatchProgressDialog(len(builds_to_package), self)
        for build in builds_to_package:
            part_label = f"Build {build.part:02d}"
            build_data = get_build_data(self.session, build)
            product_name = build_data.get('product_name', '') or "(No name)"
            progress_dialog.addBuildStatus('⏳', f"{part_label} - {product_name}")

        self.batch_packaging_worker = BatchPackagingWorker(build_specs, parent=self)
        self._setOperationState(OperationState.PACKAGING)
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
        progress_dialog.cancelButton2.clicked.connect(
            lambda: self.batch_packaging_worker.requestCancellation()
        )
        self.batch_packaging_worker.start()
        progress_dialog.exec()
        self._finalizeBatchWorkerWhenStopped()

    def _finalizeBatchWorkerWhenStopped(self):
        worker = getattr(self, "batch_packaging_worker", None)
        if worker is not None and worker.isRunning():
            QTimer.singleShot(50, self._finalizeBatchWorkerWhenStopped)
            return
        if worker is not None:
            worker.deleteLater()
            self.batch_packaging_worker = None
        self.saveSession()
        if self.operation_state is not OperationState.CLOSING:
            self._setOperationState(OperationState.IDLE)

    def _validateBuildsForPackaging(self, builds):
        validation_results = []

        for build in builds:
            issues = []
            build_data = get_build_data(self.session, build)

            if not build_data.get('store'):
                issues.append("Missing Store")
            if not build_data.get('product_name'):
                issues.append("Missing Product Name")
            try:
                validate_dim_prefix(build_data.get('prefix', ''))
            except ValueError as exc:
                issues.append(str(exc))
            try:
                validate_dim_sku(build_data.get('sku', ''))
            except ValueError as exc:
                issues.append(str(exc))
            try:
                validate_dim_part(build.part)
            except ValueError as exc:
                issues.append(str(exc))

            visible_guid = build.guid
            if build is self.current_build and hasattr(self, 'guid_input'):
                visible_guid = self.guid_input.text()
            if not _is_complete_guid(visible_guid):
                issues.append("Invalid GUID format")

            tags = {
                tag.strip().casefold()
                for tag in build_data.get('tags', '').split(',')
                if tag.strip()
            }
            if tags & {'plugin', 'software'}:
                issues.append("Plugin/Software packages are not supported")

            image_path = build_data.get('image_path', '')
            if image_path and not os.path.isfile(image_path):
                issues.append("Cover image is missing or unreadable")

            content_dir = get_build_content_dir(build.folder)
            content_has_files = False
            try:
                inventory = PackageInventory.from_content(
                    content_dir,
                    clean_support=self.support_clean_input.isChecked(),
                )
                roots = {folder.casefold() for folder in self.daz_folders}
                content_has_files = any(
                    len(member.split('/')) >= 3
                    and member.split('/')[0].casefold() == 'content'
                    and member.split('/')[1].casefold() in roots
                    for member in inventory.manifest_members
                )
            except (OSError, PackagingError, ValueError) as exc:
                issues.append(str(exc))

            if not content_has_files:
                issues.append("No packageable file exists below a recognized DAZ folder")

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

    def extractArchive(self):
        if not self.canMutateWorkspace():
            show_info(self, "Busy", "Please wait for the current operation to finish.")
            return

        archive_file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Archive File", "", "Archive Files (*.zip *.rar *.7z)"
        )
        if not archive_file_path:
            return

        self._processArchiveExtraction(archive_file_path)

    def dropExtractArchive(self, archive_file_path):
        if not self.canMutateWorkspace():
            show_info(self, "Busy", "Please wait for the current operation to finish.")
            return

        log.info("Extraction started from TreeView...")

        self._processArchiveExtraction(archive_file_path)
    
    def _processArchiveExtraction(self, archive_file_path):
        self._setOperationState(OperationState.EXTRACTING)
        self.showExtractionState(True)
        worker = ArchivePlanningWorker(
            archive_file_path,
            tuple(self.daz_folders),
            self.enable_template_detection,
            parent=self,
        )
        self.archivePlanningWorker = worker
        self._pending_planning_results += 1
        worker.resultReady.connect(self._consumeArchivePlanningResult)
        worker.finished.connect(
            lambda worker=worker: self._onPlanningWorkerFinished(worker)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _consumeArchivePlanningResult(self, result: ArchivePlanningResult):
        try:
            self._onArchivePlanningResult(result)
        finally:
            self._pending_planning_results = max(
                0, getattr(self, "_pending_planning_results", 0) - 1
            )
            if self.operation_state is OperationState.CLOSING:
                QTimer.singleShot(0, self._finishDeferredClose)

    def _onArchivePlanningResult(self, result: ArchivePlanningResult):
        if self.operation_state is OperationState.CLOSING:
            if result.plan is not None:
                result.plan.cleanup()
            self._archive_import_plan = None
            return

        if not result.succeeded or result.plan is None:
            if result.status == "cancelled":
                self.showExtractionState(False, result.message, success=False)
            else:
                self.showExtractionState(False, result.message, success=False)
                show_error(self, "Archive Analysis Failed", result.message)
            if self.operation_state is not OperationState.CLOSING:
                self._setOperationState(OperationState.IDLE)
            return

        plan = result.plan
        self._archive_import_plan = plan
        if plan.is_direct_content:
            self._extractDirectly(plan)
            return

        archive_map = {
            item.relative_path: item
            for item in (
                *plan.content_archives,
                *plan.template_archives,
                *plan.ignored_archives,
            )
        }
        self._showExtractionDialog(
            [item.relative_path for item in plan.content_archives],
            [item.relative_path for item in plan.template_archives],
            [item.relative_path for item in plan.ignored_archives],
            plan.warning,
            import_plan=plan,
            archive_map=archive_map,
        )

    def _onPlanningWorkerFinished(self, worker):
        if getattr(self, "archivePlanningWorker", None) is worker:
            self.archivePlanningWorker = None
            if (
                self.operation_state is not OperationState.CLOSING
                and getattr(self, "_archive_import_plan", None) is None
            ):
                self._setOperationState(OperationState.IDLE)

    def _askConflictPolicy(self, conflicts):
        conflicts = tuple(conflicts)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Existing Files")
        dialog.setText(
            f"{len(conflicts)} existing file(s) conflict with this import."
        )
        preview = "\n".join(
            f"• {os.path.basename(path)}" for path in conflicts[:5]
        )
        if len(conflicts) > 5:
            preview += f"\n• … and {len(conflicts) - 5} more"
        dialog.setInformativeText(
            f"{preview}\n\nThis choice is applied once to the complete import. "
            "Cancel is the safe default."
        )
        replace_button = dialog.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
        skip_button = dialog.addButton("Skip", QMessageBox.ButtonRole.ActionRole)
        cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is replace_button:
            return ConflictPolicy.REPLACE
        if clicked is skip_button:
            return ConflictPolicy.SKIP
        return ConflictPolicy.CANCEL

    def _onExtractionConflicts(self, conflicts):
        worker = self.sender()
        if (
            worker is not getattr(self, "extractionWorker", None)
            or self.operation_state is OperationState.CLOSING
        ):
            if worker is not None:
                worker.resolveConflictPolicy(ConflictPolicy.CANCEL)
            return

        policy = self._askConflictPolicy(conflicts)
        if self.operation_state is OperationState.CLOSING:
            policy = ConflictPolicy.CANCEL
        worker.resolveConflictPolicy(policy)

    def _cancelPendingArchiveImport(self, message="Extraction cancelled."):
        plan = getattr(self, "_archive_import_plan", None)
        if plan is not None:
            plan.cleanup()
        self._archive_import_plan = None
        self.showExtractionState(False, message, success=False)
        if self.operation_state is not OperationState.CLOSING:
            self._setOperationState(OperationState.IDLE)
    
    def _extractDirectly(self, import_plan):
        self._setOperationState(OperationState.EXTRACTING)
        self.showExtractionState(True)
        log.info("Extraction started...")

        current_content_dir = get_build_content_dir(self.current_build.folder)
        w = ContentExtractionWorker(
            import_plan,
            current_content_dir,
            self.template_destination,
            parent=self,
            defer_finalize=True,
            prompt_on_conflicts=True,
        )
        self.extractionWorker = w
        self._pending_extraction_results += 1
        w.resultReady.connect(self._consumeExtractionResult)
        w.conflictsDetected.connect(self._onExtractionConflicts)
        w.finished.connect(self._finishExtractionWorker)
        w.finished.connect(w.deleteLater)
        w.start()
    
    def _showExtractionDialog(
        self, content_archives, template_archives, ignored_archives, warning,
        *, import_plan, archive_map,
    ):
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
                self._cancelPendingArchiveImport("No archives were selected.")
                return

            try:
                content_list = [archive_map[path] for path in content_list]
                template_list = [archive_map[path] for path in template_list]
            except KeyError as exc:
                self._cancelPendingArchiveImport(
                    "The archive selection could not be applied."
                )
                show_error(self, "Archive Selection Error", str(exc))
                return

            self._startMultiBuildExtraction(
                content_list, template_list, import_plan=import_plan
            )
        else:
            log.info("Extraction cancelled by user")
            self._cancelPendingArchiveImport()
    
    def _startMultiBuildExtraction(
        self, content_archives, template_archives, *, import_plan,
    ):
        self._setOperationState(OperationState.EXTRACTING)
        self.showExtractionState(True)
        log.info("Starting multi-build extraction...")
        
        w = MultiBuildExtractionWorker(
            import_plan,
            content_archives,
            template_archives,
            set(self.daz_folders),
            self.session,
            self.enable_template_detection,
            self.template_destination,
            parent=self,
            defer_finalize=True,
            prompt_on_conflicts=True,
        )
        self.extractionWorker = w
        self._pending_extraction_results += 1
        w.resultReady.connect(self._consumeExtractionResult)
        w.conflictsDetected.connect(self._onExtractionConflicts)
        w.extractionProgress.connect(self.onExtractionProgress)
        w.finished.connect(self._finishExtractionWorker)
        w.finished.connect(w.deleteLater)
        w.start()
    
    def onExtractionProgress(self, message):
        log.info(f"Extraction progress: {message}")

    def _retryExtractionRollback(
        self, result: ExtractionResult
    ) -> ExtractionRollbackError | None:
        while result.rollback_pending:
            try:
                result.rollback()
            except ExtractionRollbackError as exc:
                self._pending_rollback_result = result
                reply = QMessageBox.warning(
                    self,
                    "Rollback Incomplete",
                    f"{exc}\n\nClose applications that may be using the affected "
                    "files, then choose Retry. Cancel keeps DIM-Creator open "
                    "and the workspace locked so you can retry by closing the "
                    "app again.",
                    QMessageBox.StandardButton.Retry
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Retry,
                )
                if reply != QMessageBox.StandardButton.Retry:
                    return exc
            else:
                self._pending_rollback_result = None
                return None
        self._pending_rollback_result = None
        return None

    def _consumeExtractionResult(self, result: ExtractionResult):
        try:
            self._onExtractionResult(result)
        finally:
            self._pending_extraction_results = max(
                0, getattr(self, "_pending_extraction_results", 0) - 1
            )
            if self.operation_state is OperationState.CLOSING:
                QTimer.singleShot(0, self._finishDeferredClose)

    def _onExtractionResult(self, result: ExtractionResult):
        if result.succeeded:
            session_snapshot = self.session.to_dict()
            current_build_id = getattr(self.current_build, "id", "")
            try:
                for build_data in result.new_builds:
                    build = Build.from_dict(build_data)
                    if any(item.id == build.id for item in self.session.builds):
                        raise ValueError(
                            f"Extraction returned a duplicate build: {build.id}"
                        )
                    if len(self.session.builds) >= MAX_BUILDS:
                        raise ValueError(
                            f"A session can contain at most {MAX_BUILDS} builds"
                        )
                    self.session.builds.append(build)
                if result.next_build_number is not None:
                    self.session.next_build_number = result.next_build_number
                self._revalidateAllBuildsStatus()
                self.buildListWidget.setSession(self.session)
                self.buildListWidget.refreshList()
                self.fileExplorer.refresh_view()
                if not self.saveSession():
                    raise OSError("The imported session state could not be saved")
                result.finalize()
            except Exception as exc:
                log.exception("Could not apply extraction result")
                rollback_error = None
                try:
                    result.rollback()
                except ExtractionRollbackError as rollback_exc:
                    rollback_error = rollback_exc
                    log.exception("Could not roll back extracted files")
                    rollback_error = self._retryExtractionRollback(result)
                try:
                    self.session = Session.from_dict(session_snapshot)
                    self.current_build = next(
                        (
                            build for build in self.session.builds
                            if build.id == current_build_id
                        ),
                        self.session.builds[0],
                    )
                    self.buildListWidget.setSession(self.session)
                    previous_block = self.buildListWidget.blockSignals(True)
                    try:
                        self.buildListWidget.selectBuild(self.current_build.id)
                    finally:
                        self.buildListWidget.blockSignals(previous_block)
                    self.loadBuildIntoEditor(self.current_build)
                    self.fileExplorer.setRootPath(
                        get_build_content_dir(self.current_build.folder)
                    )
                    self.fileExplorer.refresh_view()
                except Exception:
                    log.exception("Could not restore the in-memory session after import failure")
                message = str(exc)
                if rollback_error is not None:
                    message = f"{message}\n\n{rollback_error}"
                result.status = "error"
                result.message = message
                result.errors.append(message)
                self.showExtractionState(False, message, success=False)
                show_error(self, "Extraction Result Error", message)
                return

            self.showExtractionState(
                False, "Extraction completed successfully", success=True
            )
            for template_name in result.copied_templates:
                show_info(
                    self, "Template Copied",
                    f"Template <b>{template_name}</b> copied successfully.",
                    Qt.Vertical, InfoBarPosition.BOTTOM_RIGHT,
                )
            show_success(
                self, "Extraction Complete",
                self._extractionSuccessMessage(result),
                Qt.Vertical, InfoBarPosition.BOTTOM_RIGHT,
            )
        elif result.cancelled:
            self.showExtractionState(False, result.message or "Cancelled", success=False)
            show_info(self, "Extraction Cancelled", result.message or "Cancelled")
        else:
            rollback_error = None
            if result.rollback_pending:
                rollback_error = self._retryExtractionRollback(result)
                if rollback_error is not None:
                    result.message = f"{result.message}\n\n{rollback_error}"
                    result.errors.append(str(rollback_error))
            self.showExtractionState(False, result.message, success=False)
            show_error(
                self, "Extraction failed", result.message,
                Qt.Vertical, InfoBarPosition.BOTTOM_RIGHT, True, 5000,
            )

    @staticmethod
    def _extractionSuccessMessage(result: ExtractionResult) -> str:
        if result.modified_builds:
            return f"Successfully imported {len(result.modified_builds)} build(s)."
        if result.copied_templates:
            return (
                "Successfully copied "
                f"{len(result.copied_templates)} template archive(s)."
            )
        if result.skipped_files:
            return (
                "No files were imported; "
                f"{len(result.skipped_files)} existing file(s) were skipped."
            )
        return "Import completed without file changes."

    def _finishExtractionWorker(self):
        worker = self.sender()
        if getattr(self, "extractionWorker", None) is worker:
            self.extractionWorker = None
        self._archive_import_plan = None
        if (
            self.operation_state is not OperationState.CLOSING
            and getattr(self, "_pending_rollback_result", None) is None
        ):
            self._setOperationState(OperationState.IDLE)

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
    if "--smoke-test" in sys.argv:
        print(f"DIM-Creator {APP_VERSION} smoke test passed")
        sys.exit(0)
    ex = DIMPackageGUI()
    ex.show()
    sys.exit(app.exec())
