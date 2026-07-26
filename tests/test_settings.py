import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from settings import SettingsDialog, SimpleListEditor, StoreDataEditor


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

    def test_editors_refuse_to_save_when_configuration_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for editor_type, filename in (
                (StoreDataEditor, "store_data.json"),
                (SimpleListEditor, "product_tags.json"),
            ):
                with self.subTest(editor=editor_type.__name__):
                    path = os.path.join(temp_dir, filename)
                    editor = editor_type(path)

                    with self.assertRaisesRegex(OSError, "was not loaded"):
                        editor.saveData()

                    self.assertFalse(os.path.exists(path))
                    editor.deleteLater()
            self.app.processEvents()

    def test_editors_preserve_invalid_configuration_on_refused_save(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for editor_type, filename in (
                (StoreDataEditor, "store_data.json"),
                (SimpleListEditor, "daz_folders.json"),
            ):
                with self.subTest(editor=editor_type.__name__):
                    path = os.path.join(temp_dir, filename)
                    with open(path, "w", encoding="utf-8") as output:
                        output.write("{invalid")
                    editor = editor_type(path)

                    with self.assertRaisesRegex(OSError, "was not loaded"):
                        editor.saveData()

                    with open(path, "r", encoding="utf-8") as current:
                        self.assertEqual(current.read(), "{invalid")
                    editor.deleteLater()
            self.app.processEvents()

    def test_editors_save_after_successful_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "store_data.json")
            list_path = os.path.join(temp_dir, "daz_folders.json")
            with open(store_path, "w", encoding="utf-8") as output:
                output.write(
                    '{"version": 2, "data": [{"name": "Renderosity", "prefix": "RO"}]}'
                )
            with open(list_path, "w", encoding="utf-8") as output:
                output.write('{"version": 2, "data": ["Runtime"]}')
            store_editor = StoreDataEditor(store_path)
            list_editor = SimpleListEditor(list_path)

            store_editor.saveData()
            list_editor.saveData()

            self.assertTrue(store_editor._load_succeeded)
            self.assertTrue(list_editor._load_succeeded)
            store_editor.deleteLater()
            list_editor.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
