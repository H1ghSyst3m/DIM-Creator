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

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from qfluentwidgets import (
    setFont, PrimaryPushButton, PushButton, LineEdit, setTheme, Theme,
    EditableComboBox, CheckBox, InfoBarPosition, ProgressRing, ToolButton,
    StateToolTip
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
    show_error, show_info, show_success
)
from logger_utils import get_logger
from widgets import (
    ProductLineEdit, TagSelectionDialog, CustomCompactSpinBox, ImageLabel,
    FileExplorer
)
from packaging_utils import PackagingWorker
from extraction_utils import ContentExtractionWorker
from config_utils import load_configurations
from settings import SettingsDialog
from updater import UpdateManager
from version import APP_VERSION

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
        self.ensure_directory_structure()
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
        self.copy_template_files = settings.value(
            "copy_template_files", False, type=bool
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

    def closeEvent(self, event):
        try:
            self.process_button.setEnabled(False)
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
        self.cleanDIMBuildFolder()

        super().closeEvent(event)

    def ensure_directory_structure(self):
        self.dimbuild_dir = os.path.join(doc_main_dir, "DIMBuild")
        self.content_dir = os.path.join(self.dimbuild_dir, "Content")
        os.makedirs(self.content_dir, exist_ok=True)

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
        self.setMinimumSize(800, 760)
        self.setStyleSheet(tooltip_stylesheet + "DIMPackageGUI{background: rgb(32, 32, 32)}")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        main = QHBoxLayout()
        main.setSpacing(14)
        root.addLayout(main, stretch=0)

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

        actions_row = QWidget(self)
        actions_h = QHBoxLayout(actions_row)
        actions_h.setContentsMargins(0, 0, 0, 0)
        actions_h.setSpacing(8)
        self.process_button = PrimaryPushButton("Generate", self)
        self.process_button.clicked.connect(self.process)
        self.process_button.setToolTip(
            "Click to start the DIM package creation process."
        )
        self.clear_button = ToolButton(FIF.ERASE_TOOL, self)
        self.clear_button.clicked.connect(self.clearAll)
        self.clear_button.setToolTip(
            "Clear all input fields and clean the DIMBuild folder."
        )
        actions_h.addWidget(self.process_button, 0)
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

        self.fileExplorer = FileExplorer(self.dimbuild_dir, self, dimbuild_dir=self.dimbuild_dir, main_gui=self)
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

    def showSettingsDialog(self):
        dialog = SettingsDialog(self.doc_main_dir, self)

        dialog.copy_templates_checkbox.setChecked(self.copy_template_files)
        dialog.template_destination_field.setText(self.template_destination)
        dialog.auto_update_checkbox.setChecked(
            settings.value("auto_update_check", True, type=bool)
        )

        if dialog.exec():
            self.copy_template_files = dialog.copy_templates_checkbox.isChecked()
            self.template_destination = dialog.template_destination_field.text()

            settings.setValue("copy_template_files", self.copy_template_files)
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
            "Are you sure you want to clear all fields and clean the DIMBuild folder?\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.clearFields()
            self.cleanDIMBuildFolder()

    def cleanDIMBuildFolder(self):
        log.info("Attempting to clean the DIMBuild folder.")
        for filename in os.listdir(self.dimbuild_dir):
            file_path = os.path.join(self.dimbuild_dir, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path, onerror=self.handle_remove_readonly)
            except Exception as e:
                log.error(f"Failed to clean DIMBuild folder: {e}")

        log.info("DIMBuild folder successfully cleared.")
        content_folder_path = os.path.join(self.dimbuild_dir, "Content")
        if not os.path.exists(content_folder_path):
            os.makedirs(content_folder_path, exist_ok=True)

        self.fileExplorer.reinitialize_model(self.dimbuild_dir)

    def handle_remove_readonly(self, func, path, exc_info):
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

        dimbuild_dir = os.path.join(doc_main_dir, "DIMBuild")
        content_dir = os.path.join(dimbuild_dir, "Content")

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

        if not self.contentValidation(content_dir):
            reply = QMessageBox.question(
                self,
                "Content Validation Failed",
                "No recognized DAZ main folders found in the content "
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

        self.packaging_worker = PackagingWorker(
            content_dir=content_dir,
            store=store,
            product_name=product_name,
            prefix=prefix,
            sku=sku,
            product_part=product_part,
            product_tags=product_tags,
            image_path=image_path,
            clean_support=support_clean,
            guid=guid,
            destination_folder=destination_folder,
            parent=self
        )

        pw = self.packaging_worker
        self.process_button.setEnabled(False)
        self.extract_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self._setImageBusy(True, "Preparing…", 0)

        pw.progress.connect(self.updateProgress)
        pw.finished.connect(self.onPackagingFinished)
        pw.error.connect(self.onPackagingError)
        pw.start()

    def updateProgress(self, percent: int, message: str):
        self.progress_ring.setValue(percent)
        self._setImageBusy(True, f"{message}… {percent}%", percent)

    def onPackagingError(self, message: str):
        log.error(f"Packaging error: {message}")
        show_error(
            self, "Packaging Error",
            f"An error occurred:<br><small>{message}</small>",
            Qt.Horizontal, InfoBarPosition.TOP_RIGHT, True, 5000
        )
        self.resetPackagingState()

    def onPackagingFinished(self, success: bool, message: str):
        if success:
            log.info("Packaging process completed successfully.")
            self.DIMSuccessfullCreatedInfoBar()
        else:
            log.error(f"Packaging process failed: {message}")
            show_error(self, "Packaging Failed", message)

        self.resetPackagingState()

    def resetPackagingState(self):
        try:
            self._setImageBusy(False)
        except Exception:
            pass
        self.process_button.setEnabled(True)
        self.extract_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        if getattr(self, 'packaging_worker', None):
            self.packaging_worker.deleteLater()
            self.packaging_worker = None

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

        self.showExtractionState(True)
        log.info("Extraction started...")

        w = ContentExtractionWorker(
            archive_file_path,
            set(self.daz_folders),
            self.content_dir,
            self.copy_template_files,
            self.template_destination,
            parent=self
        )
        self.extractionWorker = w

        w.extractionComplete.connect(self.onExtractionComplete)
        w.extractionError.connect(self.onExtractionError)

        w.finished.connect(self._cleanupExtractionWorker)
        w.finished.connect(w.deleteLater)

        w.start()

    def dropExtractArchive(self, archive_file_path):
        if getattr(self, "extractionWorker", None) and self.extractionWorker.isRunning():
            show_info(self, "Extraction running", "Please wait for the current extraction to finish.")
            return

        self._extractionHadError = False
        self.showExtractionState(True)
        log.info("Extraction started from TreeView...")

        w = ContentExtractionWorker(
            archive_file_path,
            set(self.daz_folders),
            self.content_dir,
            self.copy_template_files,
            self.template_destination,
            parent=self
        )
        self.extractionWorker = w

        w.extractionComplete.connect(self.onExtractionComplete)
        w.extractionError.connect(self.onExtractionError)

        w.finished.connect(self._cleanupExtractionWorker)
        w.finished.connect(w.deleteLater)

        w.start()

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
            tip.move(510, 30)
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
        final_tip.move(510, 30)
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
