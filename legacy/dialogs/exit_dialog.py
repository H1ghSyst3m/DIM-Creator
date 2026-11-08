from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout
from qfluentwidgets import MessageBoxBase, SubtitleLabel, BodyLabel, PrimaryPushButton, PushButton
from logger_utils import get_logger

log = get_logger(__name__)


class ExitDialog(MessageBoxBase):
    RESULT_SAVE = 1
    RESULT_CLEAN = 2
    RESULT_CANCEL = 0
    
    def __init__(self, parent):
        super().__init__(parent)
        
        self._result = self.RESULT_CANCEL
        
        title = SubtitleLabel("Save Session?", self)
        
        message = BodyLabel(
            "Do you want to save your current session for next time, or clean up all builds?",
            self
        )
        message.setWordWrap(True)
        
        self.viewLayout.addWidget(title)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(message)
        
        self.widget.setMinimumWidth(480)
        
        self.buttonGroup.hide()
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.setContentsMargins(0, 0, 0, 0)
        
        self.saveButton = PrimaryPushButton("Save & Exit", self)
        self.saveButton.clicked.connect(self._onSaveClicked)
        self.saveButton.setDefault(True)
        self.saveButton.setAutoDefault(True)
        
        self.cleanButton = PushButton("Clean & Exit", self)
        self.cleanButton.clicked.connect(self._onCleanClicked)
        
        self.cancelButton2 = PushButton("Cancel", self)
        self.cancelButton2.clicked.connect(self._onCancelClicked)
        
        button_layout.addWidget(self.saveButton)
        button_layout.addWidget(self.cleanButton)
        button_layout.addWidget(self.cancelButton2)
        
        self.viewLayout.addSpacing(16)
        self.viewLayout.addLayout(button_layout)
        
        try:
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        except (AttributeError, RuntimeError) as exc:
            log.debug("Failed to disable window close button on ExitDialog: %s", exc)
    
    def closeEvent(self, event):
        if self._result != self.RESULT_CANCEL or hasattr(self, '_button_clicked'):
            event.accept()
        else:
            event.ignore()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._onCancelClicked()
        else:
            super().keyPressEvent(event)
    
    def _onSaveClicked(self):
        self._result = self.RESULT_SAVE
        self._button_clicked = True
        self.accept()
    
    def _onCleanClicked(self):
        self._result = self.RESULT_CLEAN
        self._button_clicked = True
        self.accept()
    
    def _onCancelClicked(self):
        self._result = self.RESULT_CANCEL
        self._button_clicked = True
        self.reject()
    
    def getResult(self) -> int:
        return self._result
