import os
import json
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QStackedWidget,
    QTableWidgetItem, QListWidgetItem, QFrame, QAbstractItemView
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from qfluentwidgets import (
    setTheme, Theme, CheckBox, LineEdit, ToolButton, PushButton, PrimaryPushButton,
    Pivot, FluentIcon as FIF, TableWidget, ListWidget, SubtitleLabel
)
from logger_utils import get_logger
from utils import tooltip_stylesheet, label_stylesheet
from version import APP_VERSION, CONFIG_VERSION

log = get_logger(__name__)


class StoreDataEditor(QWidget):
    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path

        layout = QVBoxLayout(self)

        self.table = TableWidget(self)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Name", "Prefix"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked |
            QAbstractItemView.EditTrigger.EditKeyPressed
        )

        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 100)

        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.add_btn = PushButton("Add", self)
        self.del_btn = PushButton("Delete", self)
        self.up_btn = ToolButton(FIF.UP, self)
        self.down_btn = ToolButton(FIF.DOWN, self)

        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.del_btn)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.up_btn)
        btn_layout.addWidget(self.down_btn)

        layout.addLayout(btn_layout)

        self.add_btn.clicked.connect(self.addRow)
        self.del_btn.clicked.connect(self.deleteRow)
        self.up_btn.clicked.connect(lambda: self.moveRow(-1))
        self.down_btn.clicked.connect(lambda: self.moveRow(1))

        self.loadData()

    def moveRow(self, direction: int):
        selected = self.table.currentRow()
        if selected < 0:
            return

        target_row = selected + direction
        if target_row < 0 or target_row >= self.table.rowCount():
            return

        name_item = self.table.takeItem(selected, 0)
        prefix_item = self.table.takeItem(selected, 1)

        name_item_target = self.table.takeItem(target_row, 0)
        prefix_item_target = self.table.takeItem(target_row, 1)

        self.table.setItem(selected, 0, name_item_target)
        self.table.setItem(selected, 1, prefix_item_target)
        self.table.setItem(target_row, 0, name_item)
        self.table.setItem(target_row, 1, prefix_item)

        self.table.selectRow(target_row)

    def loadData(self):
        if not os.path.exists(self.config_path):
            log.info("StoreData config not found (will be created on save): %s", self.config_path)
            return
        try:
            with open(self.config_path, 'r', encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get('data', []):
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(item.get('name', '')))
                self.table.setItem(row, 1, QTableWidgetItem(item.get('prefix', '')))
            log.info("Loaded store data from %s", self.config_path)
        except Exception as e:
            log.error("Failed to load store data from %s: %s", self.config_path, e)

    def addRow(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(""))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.editItem(self.table.item(row, 0))

    def deleteRow(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def saveData(self):
        items = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            prefix_item = self.table.item(row, 1)
            name = name_item.text().strip() if name_item else ""
            prefix = prefix_item.text().strip() if prefix_item else ""
            if name:
                items.append({"name": name, "prefix": prefix})
        try:
            with open(self.config_path, 'w', encoding="utf-8") as f:
                json.dump({"version": CONFIG_VERSION, "data": items}, f, indent=4)
            log.info("Saved store data to %s (%d items)", self.config_path, len(items))
        except Exception as e:
            log.error("Failed to save store data to %s: %s", self.config_path, e)


class SimpleListEditor(QWidget):
    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path

        layout = QVBoxLayout(self)

        self.list_widget = ListWidget(self)
        self.list_widget.setAlternatingRowColors(True)
        layout.addWidget(self.list_widget)

        input_layout = QHBoxLayout()
        self.line_edit = LineEdit(self)
        self.add_btn = PushButton("Add", self)
        input_layout.addWidget(self.line_edit)
        input_layout.addWidget(self.add_btn)
        layout.addLayout(input_layout)

        self.del_btn = PushButton("Delete Selected", self)
        layout.addWidget(self.del_btn)

        self.add_btn.clicked.connect(self.addItem)
        self.del_btn.clicked.connect(self.deleteItem)

        self.loadData()

    def loadData(self):
        if not os.path.exists(self.config_path):
            log.info("List config not found (will be created on save): %s", self.config_path)
            return
        try:
            with open(self.config_path, 'r', encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get('data', []):
                self.list_widget.addItem(QListWidgetItem(item))
            log.info("Loaded list data from %s (%d items)", self.config_path, self.list_widget.count())
        except Exception as e:
            log.error("Failed to load list data from %s: %s", self.config_path, e)

    def addItem(self):
        text = self.line_edit.text().strip()
        if text:
            self.list_widget.addItem(QListWidgetItem(text))
            self.line_edit.clear()

    def deleteItem(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def saveData(self):
        items = [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
        try:
            with open(self.config_path, 'w', encoding="utf-8") as f:
                json.dump({"version": CONFIG_VERSION, "data": items}, f, indent=4)
            log.info("Saved list data to %s (%d items)", self.config_path, len(items))
        except Exception as e:
            log.error("Failed to save list data to %s: %s", self.config_path, e)


class SettingsDialog(QDialog):
    def __init__(self, doc_main_dir: str, parent=None):
        super().__init__(parent, Qt.WindowType.WindowCloseButtonHint)
        setTheme(Theme.DARK)
        self.doc_main_dir = doc_main_dir
        self.app_version = APP_VERSION

        self.setWindowTitle("Settings")
        self.setStyleSheet(tooltip_stylesheet + "SettingsDialog{background: rgb(32, 32, 32)}" + label_stylesheet)
        self.resize(560, 460)

        main_layout = QVBoxLayout(self)

        self.pivot = Pivot(self)
        self.stack = QStackedWidget(self)
        main_layout.addWidget(self.pivot)
        main_layout.addWidget(self.stack)

        general_tab = QWidget(objectName="generalTab")
        g_layout = QVBoxLayout(general_tab)

        self.copy_templates_checkbox = CheckBox("Copy Template Archives", general_tab)
        g_layout.addWidget(self.copy_templates_checkbox)

        path_layout = QHBoxLayout()
        self.template_destination_field = LineEdit(general_tab)
        self.template_destination_field.setPlaceholderText("Default ~/Downloads")
        self.browse_button = ToolButton(FIF.FOLDER, general_tab)
        path_layout.addWidget(self.template_destination_field)
        path_layout.addWidget(self.browse_button)
        g_layout.addLayout(path_layout)

        g_layout.addStretch(1)
        self.browse_button.clicked.connect(self.selectTemplateDir)

        self.stack.addWidget(general_tab)
        self.pivot.addItem("generalTab", "General")

        config_dir = os.path.join(self.doc_main_dir, 'Config')
        os.makedirs(config_dir, exist_ok=True)

        self.store_editor = StoreDataEditor(os.path.join(config_dir, 'store_data.json'), self)
        self.store_editor.setObjectName("storesTab")
        self.stack.addWidget(self.store_editor)
        self.pivot.addItem("storesTab", "Stores")

        self.tag_editor = SimpleListEditor(os.path.join(config_dir, 'product_tags.json'), self)
        self.tag_editor.setObjectName("tagsTab")
        self.stack.addWidget(self.tag_editor)
        self.pivot.addItem("tagsTab", "Tags")

        self.folder_editor = SimpleListEditor(os.path.join(config_dir, 'daz_folders.json'), self)
        self.folder_editor.setObjectName("foldersTab")
        self.stack.addWidget(self.folder_editor)
        self.pivot.addItem("foldersTab", "DAZ Folders")

        info_tab = QWidget(objectName="infoTab")
        info_layout = QVBoxLayout(info_tab)
        info_layout.setSpacing(14)

        def add_separator():
            line = QFrame(info_tab)
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Sunken)
            info_layout.addWidget(line)

        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        about_col = QVBoxLayout()
        about_col.setSpacing(6)

        title = SubtitleLabel("DIM-Creator", info_tab)
        version_lbl = SubtitleLabel(
            f"Version: {self.app_version}", info_tab
        )
        about = SubtitleLabel(
            "A tool for creating DAZ Install Manager packages (DIM).",
            info_tab
        )

        self.auto_update_checkbox = CheckBox(
            "Check for updates on startup", info_tab
        )
        self.auto_update_checkbox.setToolTip(
            "If enabled, the app will contact GitHub and notify you when a "
            "new release is available."
        )

        for w in (title, version_lbl, about, self.auto_update_checkbox):
            w.setWordWrap(True) if hasattr(w, "setWordWrap") else None
            about_col.addWidget(w)
        about_col.addStretch(1)

        links_col = QVBoxLayout()
        links_col.setSpacing(8)

        links_header = SubtitleLabel("Links", info_tab)
        links_header.setWordWrap(True)
        links_col.addWidget(links_header)

        github_url = "https://github.com/H1ghSyst3m/DIM-Creator"
        issues_url = "https://github.com/H1ghSyst3m/DIM-Creator/issues"
        website_url = "https://example.com/dim-creator"
        license_url = "https://raw.githubusercontent.com/H1ghSyst3m/DIM-Creator/main/LICENSE"

        def safe_icon(name: str):
            return getattr(FIF, name, FIF.LINK)

        def add_link(icon_name: str, text: str, url: str, primary=False):
            btn_cls = PrimaryPushButton if primary else PushButton
            btn = btn_cls(safe_icon(icon_name), text, info_tab)
            btn.setToolTip(url)
            btn.clicked.connect(lambda _=None, u=url: (log.info("Open link: %s", u), QDesktopServices.openUrl(QUrl(u))))
            links_col.addWidget(btn)

        add_link("GITHUB", "GitHub", github_url, primary=True)
        add_link("ISSUE", "Issues", issues_url)
        add_link("HOME", "Website (WIP)", website_url)
        add_link("BOOK_SHELF", "License", license_url)
        links_col.addStretch(1)

        top_row.addLayout(about_col, 2)
        top_row.addLayout(links_col, 1)
        info_layout.addLayout(top_row)

        add_separator()

        credits_header = SubtitleLabel("Credits (license required)", info_tab)
        credits_header.setWordWrap(True)
        info_layout.addWidget(credits_header)

        credits_col = QVBoxLayout()
        credits_col.setSpacing(6)

        credits_data = [
            ("patool", "GPLv3", True,
             "https://github.com/wummel/patool/"),
            ("QFluentWidgets", "GPLv3", True,
             "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/"),
        ]

        def make_link_button(text: str, url: str):
            btn = PushButton(FIF.LINK, text, info_tab)
            btn.setToolTip(url)
            btn.clicked.connect(
                lambda _=None, u=url: (
                    log.info("Open credit link: %s", u),
                    QDesktopServices.openUrl(QUrl(u))
                )
            )
            return btn

        for name, lic, _, url in credits_data:
            credits_col.addWidget(make_link_button(f"{name} – License: {lic}", url))

        info_layout.addLayout(credits_col)
        info_layout.addStretch(1)

        self.stack.addWidget(info_tab)
        self.pivot.addItem("infoTab", "Info")

        self.pivot.currentItemChanged.connect(
            lambda k: self.stack.setCurrentWidget(self.findChild(QWidget, k))
        )
        self.pivot.setCurrentItem("generalTab")
        self.stack.setCurrentWidget(general_tab)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.save_button = PrimaryPushButton("Save", self)
        self.cancel_button = PushButton("Cancel", self)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        main_layout.addLayout(button_layout)

        self.save_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

        log.info("SettingsDialog initialized")

    def selectTemplateDir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Template Directory")
        if dir_path:
            self.template_destination_field.setText(dir_path)
            log.info("Template destination set to: %s", dir_path)

    def accept(self):
        try:
            self.store_editor.saveData()
            self.tag_editor.saveData()
            self.folder_editor.saveData()
            log.info("All settings saved from SettingsDialog")
        except Exception as e:
            log.error("Failed to save settings from SettingsDialog: %s", e)
        super().accept()
