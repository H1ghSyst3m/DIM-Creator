
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QProgressBar
from qfluentwidgets import MessageBoxBase, SubtitleLabel, BodyLabel, PushButton, StrongBodyLabel
from logger_utils import get_logger

log = get_logger(__name__)


class BatchProgressDialog(MessageBoxBase):
    
    def __init__(self, total_builds, parent=None):
        super().__init__(parent)
        
        self.total_builds = total_builds
        self.current_build_index = 0
        self.start_time = time.time()
        self.cancelled = False
        
        title = SubtitleLabel("Batch Packaging", self)
        
        overall_label = StrongBodyLabel("Overall Progress:", self)
        
        self.overall_text = BodyLabel(f"0 of {total_builds} builds complete", self)
        
        self.overall_progress = QProgressBar(self)
        self.overall_progress.setMaximum(total_builds)
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(True)
        self.overall_progress.setFormat("%v / %m (%p%)")
        self.overall_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid rgb(60, 60, 60);
                border-radius: 4px;
                background-color: rgb(45, 45, 45);
                text-align: center;
                color: white;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: rgb(0, 120, 215);
                border-radius: 3px;
            }
        """)
        
        self.elapsed_label = BodyLabel("Elapsed: 0s", self)
        
        current_label = StrongBodyLabel("Current Build:", self)
        
        self.current_build_text = BodyLabel("Waiting to start...", self)
        
        self.current_progress = QProgressBar(self)
        self.current_progress.setMaximum(100)
        self.current_progress.setValue(0)
        self.current_progress.setTextVisible(True)
        self.current_progress.setFormat("%p%")
        self.current_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid rgb(60, 60, 60);
                border-radius: 4px;
                background-color: rgb(45, 45, 45);
                text-align: center;
                color: white;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: rgb(0, 200, 100);
                border-radius: 3px;
            }
        """)
        
        self.stage_label = BodyLabel("", self)
        
        status_label = StrongBodyLabel("Build Status:", self)
        
        self.status_list = QListWidget(self)
        self.status_list.setMaximumHeight(200)
        self.status_list.setStyleSheet("""
            QListWidget {
                background-color: rgb(45, 45, 45);
                border: 1px solid rgb(60, 60, 60);
                border-radius: 6px;
                padding: 4px;
                color: white;
            }
            QListWidget::item {
                padding: 4px;
                border-radius: 4px;
                margin: 1px;
            }
        """)
        
        self.viewLayout.addWidget(title)
        self.viewLayout.addSpacing(12)
        
        self.viewLayout.addWidget(overall_label)
        self.viewLayout.addWidget(self.overall_text)
        self.viewLayout.addWidget(self.overall_progress)
        self.viewLayout.addWidget(self.elapsed_label)
        self.viewLayout.addSpacing(12)
        
        self.viewLayout.addWidget(current_label)
        self.viewLayout.addWidget(self.current_build_text)
        self.viewLayout.addWidget(self.current_progress)
        self.viewLayout.addWidget(self.stage_label)
        self.viewLayout.addSpacing(12)
        
        self.viewLayout.addWidget(status_label)
        self.viewLayout.addWidget(self.status_list)
        
        self.widget.setMinimumWidth(600)
        
        self.buttonGroup.hide()
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.setContentsMargins(0, 0, 0, 0)
        
        self.cancelButton2 = PushButton("Cancel", self)
        self.cancelButton2.clicked.connect(self._onCancelClicked)
        
        button_layout.addStretch(1)
        button_layout.addWidget(self.cancelButton2)
        
        self.viewLayout.addSpacing(16)
        self.viewLayout.addLayout(button_layout)
        
        try:
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        except (AttributeError, RuntimeError) as exc:
            log.debug("Failed to disable window close button: %s", exc)
    
    def closeEvent(self, event):
        if self.cancelled or self.current_build_index >= self.total_builds:
            event.accept()
        else:
            event.ignore()
    
    def _onCancelClicked(self):
        self.cancelled = True
        self.cancelButton2.setEnabled(False)
        self.cancelButton2.setText("Cancelling...")
        log.info("User requested cancellation of batch packaging")
    
    def isCancelled(self):
        return self.cancelled
    
    def updateOverallProgress(self, current, total):
        self.overall_progress.setValue(current)
        self.overall_text.setText(f"{current} of {total} builds complete")
        
        elapsed = int(time.time() - self.start_time)
        minutes = elapsed // 60
        seconds = elapsed % 60
        if minutes > 0:
            self.elapsed_label.setText(f"Elapsed: {minutes}m {seconds}s")
        else:
            self.elapsed_label.setText(f"Elapsed: {seconds}s")
    
    def updateBuildProgress(self, percent, stage):
        self.current_progress.setValue(percent)
        self.stage_label.setText(stage)
    
    def startBuild(self, index, part_label, product_name):
        self.current_build_index = index
        self.current_build_text.setText(f"{part_label} - {product_name}")
        self.current_progress.setValue(0)
        self.stage_label.setText("Starting...")
    
    def addBuildStatus(self, status_icon, text):
        item = QListWidgetItem(f"{status_icon} {text}")
        self.status_list.addItem(item)
        self.status_list.scrollToBottom()
    
    def updateBuildStatus(self, index, status_icon, text):
        if index < self.status_list.count():
            item = self.status_list.item(index)
            item.setText(f"{status_icon} {text}")
