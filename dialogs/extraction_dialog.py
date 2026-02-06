
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem
)
from qfluentwidgets import (
    MessageBoxBase, SubtitleLabel, BodyLabel, 
    PrimaryPushButton, PushButton, StrongBodyLabel, TransparentToolButton,
    FluentIcon as FIF
)
from logger_utils import get_logger

log = get_logger(__name__)

DIALOG_WIDTH = 800
DIALOG_HEIGHT = 550
ICON_BUTTON_SIZE = 24
REORDER_BUTTON_SIZE = 32


class ArchiveListItem(QWidget):
    
    moveRequested = Signal(str, str, str)  # archive_path, from_list, to_list
    
    def __init__(self, archive_path, list_type, parent=None, build_number=None):
        super().__init__(parent)
        self.archive_path = archive_path
        self.list_type = list_type
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        
        if list_type == "content":
            self.template_button = TransparentToolButton(FIF.DOCUMENT, parent=self)
            self.template_button.setToolTip("Move to Template list")
            self.template_button.setFixedSize(ICON_BUTTON_SIZE, ICON_BUTTON_SIZE)
            self.template_button.clicked.connect(lambda: self.moveRequested.emit(archive_path, "content", "template"))
            layout.addWidget(self.template_button)
            
            self.ignore_button = TransparentToolButton(FIF.DELETE, parent=self)
            self.ignore_button.setToolTip("Move to Ignored list")
            self.ignore_button.setFixedSize(ICON_BUTTON_SIZE, ICON_BUTTON_SIZE)
            self.ignore_button.clicked.connect(lambda: self.moveRequested.emit(archive_path, "content", "ignored"))
            layout.addWidget(self.ignore_button)
            
        elif list_type == "template":
            self.content_button = TransparentToolButton(FIF.FOLDER, parent=self)
            self.content_button.setToolTip("Move to Content list")
            self.content_button.setFixedSize(ICON_BUTTON_SIZE, ICON_BUTTON_SIZE)
            self.content_button.clicked.connect(lambda: self.moveRequested.emit(archive_path, "template", "content"))
            layout.addWidget(self.content_button)
            
            self.ignore_button = TransparentToolButton(FIF.DELETE, parent=self)
            self.ignore_button.setToolTip("Move to Ignored list")
            self.ignore_button.setFixedSize(ICON_BUTTON_SIZE, ICON_BUTTON_SIZE)
            self.ignore_button.clicked.connect(lambda: self.moveRequested.emit(archive_path, "template", "ignored"))
            layout.addWidget(self.ignore_button)
            
        elif list_type == "ignored":
            self.content_button = TransparentToolButton(FIF.FOLDER, parent=self)
            self.content_button.setToolTip("Move to Content list")
            self.content_button.setFixedSize(ICON_BUTTON_SIZE, ICON_BUTTON_SIZE)
            self.content_button.clicked.connect(lambda: self.moveRequested.emit(archive_path, "ignored", "content"))
            layout.addWidget(self.content_button)
            
            self.template_button = TransparentToolButton(FIF.DOCUMENT, parent=self)
            self.template_button.setToolTip("Move to Template list")
            self.template_button.setFixedSize(ICON_BUTTON_SIZE, ICON_BUTTON_SIZE)
            self.template_button.clicked.connect(lambda: self.moveRequested.emit(archive_path, "ignored", "template"))
            layout.addWidget(self.template_button)
        
        layout.addSpacing(5)
        
        basename = os.path.basename(archive_path)
        if list_type == "content" and build_number is not None:
            label_text = f"{build_number}: {basename}"
        else:
            label_text = basename
        self.name_label = BodyLabel(label_text)
        layout.addWidget(self.name_label, stretch=1)


class SimpleListWidget(QListWidget):
    
    reordered = Signal()
    
    def __init__(self, list_type, parent=None):
        super().__init__(parent)
        self.list_type = list_type
        
        if list_type == "content":
            self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
            self.setDefaultDropAction(Qt.DropAction.MoveAction)
            self.setDropIndicatorShown(True)
        else:
            self.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        
        self.setStyleSheet("""
            QListWidget {
                background-color: #2b2b2b;
                border: 1px solid #555;
                border-radius: 4px;
                color: white;
                padding: 4px;
            }
            QListWidget::item {
                padding: 2px;
                border-radius: 2px;
                border: none;
                outline: none;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
                border: none;
                outline: none;
            }
            QListWidget::item:hover {
                background-color: #3b3b3b;
            }
        """)
        
        self.setMinimumHeight(100)
    
    def dropEvent(self, event):
        super().dropEvent(event)
        if self.list_type == "content":
            self.reordered.emit()


class ExtractionDialog(MessageBoxBase):
    
    RESULT_EXTRACT = 1
    RESULT_CANCEL = 0
    
    def __init__(self, content_archives, template_archives, ignored_archives, 
                 enable_template_detection, existing_builds, parent):
        super().__init__(parent)
        
        self._result = self.RESULT_CANCEL
        self.enable_template_detection = enable_template_detection
        self.existing_builds = existing_builds
        self.warning_message = None
        
        self.content_archives = list(content_archives)
        self.template_archives = list(template_archives)
        self.ignored_archives = list(ignored_archives)
        
        self._buildUI()
        
        self._populateLists()
        
        self._updatePreview()
    
    def _buildUI(self):
        title = SubtitleLabel("Extract Multiple Archives", self)
        self.viewLayout.addWidget(title)
        self.viewLayout.addSpacing(8)
        
        desc = BodyLabel(
            "Review the archives that will be extracted. "
            "Content archives will create builds in order.",
            self
        )
        desc.setWordWrap(True)
        self.viewLayout.addWidget(desc)
        self.viewLayout.addSpacing(12)
        
        lists_layout = QHBoxLayout()
        lists_layout.setSpacing(12)
        
        content_container = self._createListContainer(
            "Content Archives",
            "Will create/append to builds",
            "content"
        )
        lists_layout.addWidget(content_container, stretch=2)
        
        template_container = self._createListContainer(
            "Template Archives",
            "Will copy to Downloads",
            "template"
        )
        lists_layout.addWidget(template_container, stretch=1)
        
        ignored_container = self._createListContainer(
            "Ignored Archives",
            "Will not be processed",
            "ignored"
        )
        lists_layout.addWidget(ignored_container, stretch=1)
        
        self.viewLayout.addLayout(lists_layout)
        self.viewLayout.addSpacing(12)
        
        preview_label = StrongBodyLabel("Preview:", self)
        self.viewLayout.addWidget(preview_label)
        
        self.preview_text = BodyLabel("", self)
        self.preview_text.setWordWrap(True)
        self.preview_text.setStyleSheet("color: #999;")
        self.viewLayout.addWidget(self.preview_text)
        self.viewLayout.addSpacing(16)
        
        self.buttonGroup.hide()
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        
        self.extractButton = PrimaryPushButton("Extract", self)
        self.extractButton.clicked.connect(self._onExtractClicked)
        self.extractButton.setDefault(True)
        
        self.cancelButton2 = PushButton("Cancel", self)
        self.cancelButton2.clicked.connect(self._onCancelClicked)
        
        button_layout.addWidget(self.extractButton)
        button_layout.addWidget(self.cancelButton2)
        
        self.viewLayout.addLayout(button_layout)
        
        self._updateExtractButtonState()
        
        self.widget.setMinimumWidth(DIALOG_WIDTH)
        self.widget.setMinimumHeight(DIALOG_HEIGHT)
    
    def _createListContainer(self, title, subtitle, list_type):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        title_label = StrongBodyLabel(title, container)
        layout.addWidget(title_label)
        
        subtitle_label = BodyLabel(subtitle, container)
        subtitle_label.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(subtitle_label)
        
        list_row = QHBoxLayout()
        list_row.setSpacing(4)
        
        list_widget = SimpleListWidget(list_type, container)
        list_row.addWidget(list_widget, stretch=1)
        
        if list_type == "content":
            button_col = QVBoxLayout()
            button_col.setSpacing(4)
            button_col.addStretch(1)
            
            self.move_up_button = TransparentToolButton(FIF.UP, container)
            self.move_up_button.setToolTip("Move selected archive up")
            self.move_up_button.setFixedSize(REORDER_BUTTON_SIZE, REORDER_BUTTON_SIZE)
            self.move_up_button.clicked.connect(self._moveContentUp)
            button_col.addWidget(self.move_up_button)
            
            self.move_down_button = TransparentToolButton(FIF.DOWN, container)
            self.move_down_button.setToolTip("Move selected archive down")
            self.move_down_button.setFixedSize(REORDER_BUTTON_SIZE, REORDER_BUTTON_SIZE)
            self.move_down_button.clicked.connect(self._moveContentDown)
            button_col.addWidget(self.move_down_button)
            
            button_col.addStretch(1)
            list_row.addLayout(button_col)
        
        layout.addLayout(list_row, stretch=1)
        
        if list_type == "content":
            list_widget.reordered.connect(self._onContentReordered)
        
        if list_type == "content":
            self.content_list = list_widget
        elif list_type == "template":
            self.template_list = list_widget
        elif list_type == "ignored":
            self.ignored_list = list_widget
        
        return container
    
    def _populateLists(self):
        for i, archive_path in enumerate(self.content_archives, 1):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, archive_path)
            widget = ArchiveListItem(archive_path, "content", build_number=i)
            item.setSizeHint(widget.sizeHint())
            self.content_list.addItem(item)
            
            widget.moveRequested.connect(self._moveArchive)
            self.content_list.setItemWidget(item, widget)
        
        for archive_path in self.template_archives:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, archive_path)
            
            widget = ArchiveListItem(archive_path, "template")
            item.setSizeHint(widget.sizeHint())
            self.template_list.addItem(item)
            widget.moveRequested.connect(self._moveArchive)
            self.template_list.setItemWidget(item, widget)
        
        for archive_path in self.ignored_archives:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, archive_path)
            
            widget = ArchiveListItem(archive_path, "ignored")
            item.setSizeHint(widget.sizeHint())
            self.ignored_list.addItem(item)
            widget.moveRequested.connect(self._moveArchive)
            self.ignored_list.setItemWidget(item, widget)
    
    def _updatePreview(self):
        preview_parts = []
        
        if self.content_archives:
            preview_parts.append(f"<b>Will create/append to {len(self.content_archives)} build(s):</b>")
            for i, archive_path in enumerate(self.content_archives, 1):
                basename = os.path.basename(archive_path)
                
                existing_build = self._findBuildByBuildNumber(i)
                if existing_build:
                    action = f"Append to {existing_build.folder}"
                else:
                    action = f"Create new build (Build {i})"
                
                preview_parts.append(f"  • Build {i}: {basename} → {action}")
        
        if self.template_archives:
            preview_parts.append("")
            preview_parts.append(f"<b>Will copy {len(self.template_archives)} template(s) to Downloads:</b>")
            for archive_path in self.template_archives:
                basename = os.path.basename(archive_path)
                preview_parts.append(f"  • {basename}")
        
        if self.ignored_archives:
            preview_parts.append("")
            preview_parts.append(f"<b>Will ignore {len(self.ignored_archives)} archive(s):</b>")
            for archive_path in self.ignored_archives:
                basename = os.path.basename(archive_path)
                preview_parts.append(f"  • {basename}")
        
        preview_html = "<br>".join(preview_parts)
        self.preview_text.setText(preview_html)
    
    def _findBuildByBuildNumber(self, build_number):
        for build in self.existing_builds:
            if build.part == build_number:
                return build
        return None
    
    def _moveArchive(self, archive_path, from_list, to_list):
        log.info(f"Moving archive {os.path.basename(archive_path)} from {from_list} to {to_list}")
        
        if from_list == "content":
            self.content_archives.remove(archive_path)
        elif from_list == "template":
            self.template_archives.remove(archive_path)
        elif from_list == "ignored":
            self.ignored_archives.remove(archive_path)
        
        if to_list == "content":
            self.content_archives.append(archive_path)
        elif to_list == "template":
            self.template_archives.append(archive_path)
        elif to_list == "ignored":
            self.ignored_archives.append(archive_path)
        
        self._refreshLists()
        
        self._updatePreview()
        
        self._updateExtractButtonState()
    
    def _onContentReordered(self):
        log.info("Content list reordered via drag-drop")
        
        new_order = []
        for i in range(self.content_list.count()):
            item = self.content_list.item(i)
            archive_path = item.data(Qt.ItemDataRole.UserRole)
            new_order.append(archive_path)
        
        self.content_archives = new_order
        
        self._refreshContentList()
        
        self._updatePreview()
    
    def _refreshContentList(self):
        self.content_list.clear()
        for i, archive_path in enumerate(self.content_archives, 1):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, archive_path)
            widget = ArchiveListItem(archive_path, "content", build_number=i)
            item.setSizeHint(widget.sizeHint())
            self.content_list.addItem(item)
            
            widget.moveRequested.connect(self._moveArchive)
            self.content_list.setItemWidget(item, widget)
        
        self._updateExtractButtonState()
    
    def _moveContentUp(self):
        current_row = self.content_list.currentRow()
        
        if current_row <= 0:
            return
        
        archive_path = self.content_archives[current_row]
        
        self.content_archives[current_row], self.content_archives[current_row - 1] = \
            self.content_archives[current_row - 1], self.content_archives[current_row]
        
        self._refreshContentList()
        
        self.content_list.setCurrentRow(current_row - 1)
        
        self._updatePreview()
        
        log.info(f"Moved {os.path.basename(archive_path)} up to position {current_row}")
    
    def _moveContentDown(self):
        current_row = self.content_list.currentRow()
        
        if current_row < 0 or current_row >= len(self.content_archives) - 1:
            return
        
        archive_path = self.content_archives[current_row]
        
        self.content_archives[current_row], self.content_archives[current_row + 1] = \
            self.content_archives[current_row + 1], self.content_archives[current_row]
        
        self._refreshContentList()
        
        self.content_list.setCurrentRow(current_row + 1)
        
        self._updatePreview()
        
        log.info(f"Moved {os.path.basename(archive_path)} down to position {current_row + 2}")
    
    def _updateExtractButtonState(self):
        has_content = len(self.content_archives) > 0
        has_template = len(self.template_archives) > 0
        
        self.extractButton.setEnabled(has_content or has_template)
    
    def _refreshLists(self):
        self.content_list.clear()
        self.template_list.clear()
        self.ignored_list.clear()
        
        self._populateLists()
        
        self._updateExtractButtonState()
    
    def closeEvent(self, event):
        if hasattr(self, '_button_clicked'):
            event.accept()
        else:
            event.ignore()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._onCancelClicked()
        else:
            super().keyPressEvent(event)
    
    def _onExtractClicked(self):
        self._result = self.RESULT_EXTRACT
        self._button_clicked = True
        self.accept()
    
    def _onCancelClicked(self):
        self._result = self.RESULT_CANCEL
        self._button_clicked = True
        self.reject()
    
    def getResult(self):
        return self._result
    
    def getArchiveLists(self):
        return self.content_archives, self.template_archives, self.ignored_archives
    
    def setWarningMessage(self, message):
        self.warning_message = message
        if message:
            warning_label = BodyLabel(f"⚠️ {message}", self)
            warning_label.setStyleSheet("color: #ff9800; font-weight: bold;")
            warning_label.setWordWrap(True)
            self.viewLayout.insertWidget(2, warning_label)
            self.viewLayout.insertSpacing(3, 8)
