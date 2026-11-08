
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem
from qfluentwidgets import MessageBoxBase, SubtitleLabel, BodyLabel, PrimaryPushButton, PushButton, StrongBodyLabel
from logger_utils import get_logger

log = get_logger(__name__)


class ValidationDialog(MessageBoxBase):
    
    RESULT_PACKAGE_ALL = 1
    RESULT_PACKAGE_VALID = 2
    RESULT_CANCEL = 0
    
    def __init__(self, builds_validation, session=None, parent=None):
        super().__init__(parent)
        
        self.builds_validation = builds_validation
        self.session = session
        self._result = self.RESULT_CANCEL
        
        self.has_duplicate_guids = self._check_duplicate_guids()
        
        title = SubtitleLabel("Package Validation", self)
        
        total = len(builds_validation)
        ready_count = sum(1 for b in builds_validation if b['status'] == 'ready')
        incomplete_count = sum(1 for b in builds_validation if b['status'] == 'incomplete')
        empty_count = sum(1 for b in builds_validation if b['status'] == 'empty')
        
        summary_text = f"Total builds: {total} | ✅ Ready: {ready_count} | ⚠️ Incomplete: {incomplete_count} | 📭 Empty: {empty_count}"
        
        if self.has_duplicate_guids:
            summary_text += "\n\n⚠️ ERROR: Duplicate GUIDs detected! Packaging is blocked."
            
            if self.duplicate_guid_details:
                duplicate_parts = []
                for guid, builds_list in self.duplicate_guid_details.items():
                    part_nums = [f"Build {b.part:02d}" for b in builds_list]
                    duplicate_parts.append(f"{', '.join(part_nums)}")
                if duplicate_parts:
                    summary_text += f"\nDuplicate in: {'; '.join(duplicate_parts)}"
        
        summary = BodyLabel(summary_text, self)
        summary.setWordWrap(True)
        
        self.buildList = QListWidget(self)
        self.buildList.setMaximumHeight(300)
        self.buildList.setStyleSheet("""
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
        
        for build_data in builds_validation:
            build = build_data['build']
            status = build_data['status']
            issues = build_data['issues']
            
            status_icons = {
                'ready': '✅',
                'incomplete': '⚠️',
                'empty': '📭'
            }
            icon = status_icons.get(status, '📭')
            
            product_name = None
            if self.session:
                try:
                    from build_manager import get_build_data
                    effective_data = get_build_data(self.session, build)
                    product_name = effective_data.get('product_name', '')
                except Exception as e:
                    log.warning(f"Failed to get effective build data for build {build.part}: {e}")
            
            if not product_name:
                product_name = getattr(build, 'product_name', None)
            
            if product_name:
                item_text = f"{icon} Build {build.part:02d} - {product_name}"
            else:
                item_text = f"{icon} Build {build.part:02d}"
            
            if issues:
                item_text += "\n   " + "\n   ".join(['• ' + issue for issue in issues])
            
            item = QListWidgetItem(item_text)
            self.buildList.addItem(item)
        
        self.viewLayout.addWidget(title)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(summary)
        self.viewLayout.addSpacing(12)
        
        list_label = StrongBodyLabel("Builds:", self)
        self.viewLayout.addWidget(list_label)
        self.viewLayout.addWidget(self.buildList)
        
        self.widget.setMinimumWidth(600)
        
        self.buttonGroup.hide()
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.setContentsMargins(0, 0, 0, 0)
        
        self.packageAllButton = PrimaryPushButton("Package All", self)
        self.packageAllButton.clicked.connect(self._onPackageAllClicked)
        
        self.packageValidButton = PushButton("Package Valid Only", self)
        self.packageValidButton.clicked.connect(self._onPackageValidClicked)
        
        self.cancelButton2 = PushButton("Cancel", self)
        self.cancelButton2.clicked.connect(self._onCancelClicked)
        
        if self.has_duplicate_guids:
            self.packageAllButton.setEnabled(False)
            self.packageValidButton.setEnabled(False)
            self.packageAllButton.setToolTip("Cannot package: Duplicate GUIDs detected")
            self.packageValidButton.setToolTip("Cannot package: Duplicate GUIDs detected")
        else:
            if incomplete_count == 0 and empty_count == 0:
                self.packageValidButton.setEnabled(False)
                self.packageValidButton.setToolTip("No incomplete or empty builds to skip")
        
        button_layout.addWidget(self.packageAllButton)
        button_layout.addWidget(self.packageValidButton)
        button_layout.addWidget(self.cancelButton2)
        
        self.viewLayout.addSpacing(16)
        self.viewLayout.addLayout(button_layout)
    
    def _check_duplicate_guids(self):
        guid_to_builds = {}
        for entry in self.builds_validation:
            build = entry.get('build')
            if build is None:
                continue
            guid = getattr(build, 'guid', None)
            if guid is None:
                continue
            guid_to_builds.setdefault(guid, []).append(build)
        
        duplicate_guid_details = {
            guid: builds
            for guid, builds in guid_to_builds.items()
            if len(builds) > 1
        }
        
        self.duplicate_guid_details = duplicate_guid_details
        
        if duplicate_guid_details:
            for guid, builds in duplicate_guid_details.items():
                try:
                    build_labels = [f"Build {b.part:02d}" for b in builds]
                except Exception:
                    build_labels = [repr(b) for b in builds]
                log.error(
                    "Duplicate GUID detected: %s used by builds: %s",
                    guid,
                    ", ".join(build_labels),
                )
        
        return bool(duplicate_guid_details)
    
    def _onPackageAllClicked(self):
        self._result = self.RESULT_PACKAGE_ALL
        self.accept()
    
    def _onPackageValidClicked(self):
        self._result = self.RESULT_PACKAGE_VALID
        self.accept()
    
    def _onCancelClicked(self):
        self._result = self.RESULT_CANCEL
        self.reject()
    
    def getResult(self) -> int:
        return self._result
