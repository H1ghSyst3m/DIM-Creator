
import os
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl, Qt
from qfluentwidgets import MessageBoxBase, SubtitleLabel, BodyLabel, PrimaryPushButton, PushButton, StrongBodyLabel
from logger_utils import get_logger
from utils import format_file_size

log = get_logger(__name__)


class ResultSummaryDialog(MessageBoxBase):
    
    def __init__(self, results, destination_folder, total_time, session, parent=None):
        super().__init__(parent)
        
        self.results = results
        self.destination_folder = destination_folder
        self.session = session
        
        total = len(results)
        successful = sum(1 for r in results if r['success'] and not r.get('skipped', False))
        failed = sum(1 for r in results if not r['success'] and not r.get('skipped', False))
        skipped = sum(1 for r in results if r.get('skipped', False))
        
        minutes = int(total_time) // 60
        seconds = int(total_time) % 60
        if minutes > 0:
            time_str = f"{minutes}m {seconds}s"
        else:
            time_str = f"{seconds}s"
        
        title = SubtitleLabel("Batch Packaging Complete", self)
        
        summary_text = f"<span style='color: white;'>Total processed: {total}</span> | "
        summary_text += f"<span style='color: #00c864;'>✅ Successful: {successful}</span> | "
        summary_text += f"<span style='color: #e74856;'>❌ Failed: {failed}</span> | "
        summary_text += f"<span style='color: #999;'>⏭️ Skipped: {skipped}</span><br>"
        summary_text += f"<span style='color: white;'>Total time: {time_str}</span>"
        
        summary = BodyLabel(summary_text, self)
        summary.setTextFormat(Qt.TextFormat.RichText)
        summary.setWordWrap(True)
        
        details_label = StrongBodyLabel("Build Details:", self)
        
        self.details_list = QListWidget(self)
        self.details_list.setMaximumHeight(300)
        self.details_list.setStyleSheet("""
            QListWidget {
                background-color: rgb(45, 45, 45);
                border: 1px solid rgb(60, 60, 60);
                border-radius: 6px;
                padding: 4px;
                color: white;
            }
            QListWidget::item {
                padding: 6px;
                border-radius: 4px;
                margin: 2px;
            }
            QListWidget::item:hover {
                background-color: rgb(55, 55, 55);
            }
        """)
        
        for result in results:
            build = result['build']
            success = result['success']
            message = result['message']
            file_size = result.get('file_size')
            output_path = result.get('output_path')
            is_skipped = result.get('skipped', False)
            
            if is_skipped:
                icon = '⏭️'
            elif success:
                icon = '✅'
            else:
                icon = '❌'
            
            part_label = f"Build {build.part:02d}"
            
            if self.session:
                from build_manager import get_build_data
                build_data = get_build_data(self.session, build)
                product_name = build_data.get('product_name', '') or "(No name)"
            else:
                product_name = build.product_name or "(No name)"
            
            item_text = f"{icon} {part_label} - {product_name}"
            
            if is_skipped:
                item_text += f"\n   {message}"
            elif success:
                if file_size is None or file_size < 0:
                    item_text += "\n   Size: Unknown"
                else:
                    size_str = format_file_size(file_size)
                    item_text += f"\n   Size: {size_str}"
                if output_path:
                    item_text += f"\n   {output_path}"
            elif not success:
                item_text += f"\n   Error: {message}"
            
            item = QListWidgetItem(item_text)
            
            if output_path:
                item.setData(Qt.ItemDataRole.UserRole, output_path)
            
            self.details_list.addItem(item)
        
        self.details_list.itemDoubleClicked.connect(self._onItemDoubleClicked)
        
        self.viewLayout.addWidget(title)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(summary)
        self.viewLayout.addSpacing(12)
        self.viewLayout.addWidget(details_label)
        self.viewLayout.addWidget(self.details_list)
        
        self.widget.setMinimumWidth(700)
        
        self.buttonGroup.hide()
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.setContentsMargins(0, 0, 0, 0)
        
        self.openFolderButton = PrimaryPushButton("Open Destination Folder", self)
        self.openFolderButton.clicked.connect(self._onOpenFolderClicked)
        
        self.closeButton2 = PushButton("Close", self)
        self.closeButton2.clicked.connect(self.accept)
        
        button_layout.addWidget(self.openFolderButton)
        button_layout.addWidget(self.closeButton2)
        
        self.viewLayout.addSpacing(16)
        self.viewLayout.addLayout(button_layout)
    
    def _onOpenFolderClicked(self):
        if os.path.exists(self.destination_folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.destination_folder))
            log.info(f"Opened destination folder: {self.destination_folder}")
        else:
            log.warning(f"Destination folder does not exist: {self.destination_folder}")
    
    def _onItemDoubleClicked(self, item):
        output_path = item.data(Qt.ItemDataRole.UserRole)
        if output_path and os.path.exists(output_path):
            folder = os.path.dirname(output_path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            log.info(f"Opened file location: {folder}")
