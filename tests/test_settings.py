import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from settings import SettingsDialog


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_save_failure_keeps_dialog_unaccepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dialog = SettingsDialog(temp_dir)
            accepted = []
            dialog.accepted.connect(lambda: accepted.append(True))

            with (
                patch.object(
                    dialog.store_editor,
                    "saveData",
                    side_effect=OSError("disk full"),
                ),
                patch.object(QMessageBox, "critical") as critical,
            ):
                dialog.accept()

            self.assertEqual(accepted, [])
            critical.assert_called_once()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
