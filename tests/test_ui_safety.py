import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QObject, QTimer, Qt, Slot
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

import app as app_module
import extraction_utils as extraction_module
import widgets as widgets_module
from app import DIMPackageGUI
from dialogs.exit_dialog import ExitDialog
from extraction_utils import (
    ConflictPolicy,
    ContentExtractionWorker,
    ExtractionResult,
    ExtractionRollbackError,
)
from operation_state import OperationState
from utils import (
    has_reparse_component,
    move_to_trash,
    safe_child_path,
    validate_windows_name,
)
from widgets import FileExplorer, ImageLabel


class _StubWidget:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, enabled):
        self.enabled = enabled


class _StubImage(_StubWidget):
    def __init__(self):
        super().__init__()
        self.aborted = 0

    def _abort_active_download(self):
        self.aborted += 1


class _StubTimer:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _StubEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


class OperationStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_entering_busy_state_stops_pending_save_and_cover_download(self):
        gui = SimpleNamespace(
            operation_state=OperationState.IDLE,
            _save_timer=_StubTimer(),
            image_label=_StubImage(),
            tags_button=_StubWidget(),
            generate_guid_button=_StubWidget(),
            sync_container_widget=_StubWidget(),
        )

        DIMPackageGUI._setOperationState(gui, OperationState.PACKAGING)

        self.assertIs(gui.operation_state, OperationState.PACKAGING)
        self.assertEqual(gui._save_timer.stopped, 1)
        self.assertEqual(gui.image_label.aborted, 1)
        self.assertFalse(gui.image_label.enabled)
        self.assertFalse(gui.tags_button.enabled)
        self.assertFalse(gui.generate_guid_button.enabled)
        self.assertFalse(gui.sync_container_widget.enabled)

    def test_by_date_packaging_rejects_a_build_target_before_creating_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            builds_root = Path(temp_dir) / "Builds"
            content_root = builds_root / "Build001" / "Content"
            content_root.mkdir(parents=True)
            build = SimpleNamespace(part=1, folder="Build001")
            gui = SimpleNamespace(
                canMutateWorkspace=lambda: True,
                session=SimpleNamespace(builds=[build]),
                _validateBuildsForPackaging=lambda builds: [
                    {"build": build, "status": "ready"}
                ],
                last_destination_folder=str(content_root),
            )
            dialog = SimpleNamespace(
                exec=lambda: 1,
                getResult=lambda: app_module.ValidationDialog.RESULT_PACKAGE_ALL,
            )
            fake_settings = SimpleNamespace(
                setValue=lambda *args, **kwargs: None,
                value=lambda *args, **kwargs: "By Date",
            )

            with (
                patch.object(app_module, "ValidationDialog", return_value=dialog),
                patch.object(
                    app_module.QFileDialog,
                    "getExistingDirectory",
                    return_value=str(content_root),
                ),
                patch.object(app_module, "settings", fake_settings),
                patch.object(
                    app_module,
                    "get_build_content_dir",
                    return_value=str(content_root),
                ),
                patch.object(app_module, "show_error") as show_error,
            ):
                DIMPackageGUI._packageBuilds(gui, [build])

            expected_date = content_root / app_module.date.today().strftime("%Y-%m-%d")
            self.assertFalse(expected_date.exists())
            show_error.assert_called_once()

    def test_cancel_after_deferred_close_restores_idle_ui(self):
        class CancelDialog:
            RESULT_SAVE = 1
            RESULT_CANCEL = 2
            RESULT_CLEAN = 3

            def __init__(self, _parent):
                pass

            def exec(self):
                return 0

            def getResult(self):
                return self.RESULT_CANCEL

        states = []
        gui = SimpleNamespace(
            _close_ready=True,
            _runningWorkers=lambda: [],
            hasUserMadeChanges=lambda: True,
            _setOperationState=states.append,
        )
        event = _StubEvent()

        with patch.object(app_module, "ExitDialog", CancelDialog):
            DIMPackageGUI.closeEvent(gui, event)

        self.assertTrue(event.ignored)
        self.assertFalse(gui._close_ready)
        self.assertEqual(states, [OperationState.IDLE])

    def test_session_save_failure_blocks_close(self):
        states = []
        progress = SimpleNamespace(hide=lambda: None, setValue=lambda _value: None)
        gui = SimpleNamespace(
            _close_ready=False,
            _runningWorkers=lambda: [],
            hasUserMadeChanges=lambda: False,
            _setOperationState=states.append,
            progress_ring=progress,
            saveSettings=lambda: None,
            saveSession=lambda: False,
        )
        event = _StubEvent()

        DIMPackageGUI.closeEvent(gui, event)

        self.assertTrue(event.ignored)
        self.assertFalse(gui._close_ready)
        self.assertEqual(
            states, [OperationState.CLOSING, OperationState.IDLE]
        )

    def test_deferred_close_waits_for_queued_extraction_result(self):
        calls = []
        gui = SimpleNamespace(
            _pending_extraction_results=1,
            _runningWorkers=lambda: [],
            _requestWorkerCancellation=lambda: calls.append("cancel"),
            _close_poll_timer=SimpleNamespace(
                start=lambda interval: calls.append(("poll", interval))
            ),
            close=lambda: calls.append("close"),
            _close_ready=False,
        )

        DIMPackageGUI._finishDeferredClose(gui)

        self.assertFalse(gui._close_ready)
        self.assertEqual(calls, ["cancel", ("poll", 100)])

    def test_incomplete_rollback_keeps_workspace_locked_during_close(self):
        calls = []
        result = SimpleNamespace(rollback_pending=True)
        gui = SimpleNamespace(
            _pending_rollback_result=result,
            _close_ready=True,
            _setOperationState=calls.append,
            close=lambda: calls.append("close"),
        )

        DIMPackageGUI._finishDeferredClose(gui)

        self.assertFalse(gui._close_ready)
        self.assertEqual(calls, [OperationState.EXTRACTING])

    def test_failed_rollback_retry_is_retained_when_user_cancels(self):
        failure = ExtractionRollbackError(
            [(r"C:\locked.duf", PermissionError("locked"))]
        )

        class Result:
            rollback_pending = True

            @staticmethod
            def rollback():
                raise failure

        gui = SimpleNamespace(_pending_rollback_result=None)
        result = Result()

        with patch.object(
            app_module.QMessageBox,
            "warning",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            returned = DIMPackageGUI._retryExtractionRollback(gui, result)

        self.assertIs(returned, failure)
        self.assertIs(gui._pending_rollback_result, result)

    def test_successful_rollback_retry_releases_retained_result(self):
        class Result:
            rollback_pending = True

            def rollback(self):
                self.rollback_pending = False

        gui = SimpleNamespace(_pending_rollback_result=object())

        returned = DIMPackageGUI._retryExtractionRollback(gui, Result())

        self.assertIsNone(returned)
        self.assertIsNone(gui._pending_rollback_result)

    def test_consuming_result_releases_deferred_close(self):
        calls = []
        gui = SimpleNamespace(
            _pending_extraction_results=1,
            operation_state=OperationState.CLOSING,
            _onExtractionResult=lambda result: calls.append(result),
            _finishDeferredClose=lambda: calls.append("finish-close"),
        )
        result = object()

        with patch.object(app_module.QTimer, "singleShot", side_effect=lambda _, fn: fn()):
            DIMPackageGUI._consumeExtractionResult(gui, result)

        self.assertEqual(gui._pending_extraction_results, 0)
        self.assertEqual(calls, [result, "finish-close"])

    def test_consuming_planning_result_releases_deferred_close(self):
        calls = []
        gui = SimpleNamespace(
            _pending_planning_results=1,
            operation_state=OperationState.CLOSING,
            _onArchivePlanningResult=lambda result: calls.append(result),
            _finishDeferredClose=lambda: calls.append("finish-close"),
        )
        result = object()

        with patch.object(app_module.QTimer, "singleShot", side_effect=lambda _, fn: fn()):
            DIMPackageGUI._consumeArchivePlanningResult(gui, result)

        self.assertEqual(gui._pending_planning_results, 0)
        self.assertEqual(calls, [result, "finish-close"])

    def test_failed_planning_result_restores_idle_state(self):
        states = []
        gui = SimpleNamespace(
            operation_state=OperationState.EXTRACTING,
            _setOperationState=states.append,
            showExtractionState=lambda *_args, **_kwargs: None,
        )
        result = SimpleNamespace(
            succeeded=False,
            plan=None,
            status="cancelled",
            message="Cancelled",
        )

        DIMPackageGUI._onArchivePlanningResult(gui, result)

        self.assertEqual(states, [OperationState.IDLE])

    def test_pre_worker_import_cancel_cleans_plan_tooltip_and_busy_state(self):
        plan = SimpleNamespace(cleaned=0)
        plan.cleanup = lambda: setattr(plan, "cleaned", plan.cleaned + 1)
        extraction_states = []
        operation_states = []
        gui = SimpleNamespace(
            _archive_import_plan=plan,
            operation_state=OperationState.EXTRACTING,
            showExtractionState=lambda *args, **kwargs: extraction_states.append(
                (args, kwargs)
            ),
            _setOperationState=operation_states.append,
        )

        DIMPackageGUI._cancelPendingArchiveImport(gui, "Cancelled")

        self.assertEqual(plan.cleaned, 1)
        self.assertIsNone(gui._archive_import_plan)
        self.assertEqual(extraction_states, [((False, "Cancelled"), {"success": False})])
        self.assertEqual(operation_states, [OperationState.IDLE])

    def test_extraction_success_messages_report_actual_changes(self):
        cases = (
            (
                ExtractionResult("success", modified_builds=["Build001", "Build002"]),
                "Successfully imported 2 build(s).",
            ),
            (
                ExtractionResult("success", copied_templates=["Template.zip"]),
                "Successfully copied 1 template archive(s).",
            ),
            (
                ExtractionResult("success", skipped_files=["one", "two"]),
                "No files were imported; 2 existing file(s) were skipped.",
            ),
            (
                ExtractionResult("success"),
                "Import completed without file changes.",
            ),
        )

        for result, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    DIMPackageGUI._extractionSuccessMessage(result),
                    expected,
                )

    def test_conflict_decision_reaches_a_running_qthread(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content = Path(temp_dir) / "Build001" / "Content"
            runtime = content / "Runtime"
            templates = Path(temp_dir) / "Templates"
            runtime.mkdir(parents=True)
            existing = runtime / "existing.txt"
            existing.write_bytes(b"old")
            archive = Path(temp_dir) / "product.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("Runtime/existing.txt", b"replacement")

            worker = ContentExtractionWorker(
                str(archive),
                {"Runtime"},
                str(content),
                True,
                str(templates),
                prompt_on_conflicts=True,
            )

            class Receiver(QObject):
                def __init__(self):
                    super().__init__()
                    self.conflicts = []

                @Slot(object)
                def resolve(self, conflicts):
                    self.conflicts.append(tuple(conflicts))
                    worker.resolveConflictPolicy(ConflictPolicy.SKIP)

            receiver = Receiver()
            worker.conflictsDetected.connect(receiver.resolve)
            loop = QEventLoop()
            timed_out = []
            timer = QTimer()
            timer.setSingleShot(True)

            def abort_wait():
                timed_out.append(True)
                worker.requestCancellation()
                loop.quit()

            timer.timeout.connect(abort_wait)
            worker.finished.connect(loop.quit)
            with patch.object(extraction_module, "MIN_FREE_SPACE_BYTES", 0):
                worker.start()
                timer.start(5000)
                loop.exec()
                if worker.isRunning():
                    worker.requestCancellation()
                    worker.wait(2000)
            timer.stop()

            self.assertEqual(timed_out, [])
            self.assertEqual(len(receiver.conflicts), 1)
            self.assertEqual(worker.result.status, "success")
            self.assertEqual(worker.result.modified_builds, [])
            self.assertEqual(existing.read_bytes(), b"old")
            worker.deleteLater()

    def test_session_cleanup_deletes_managed_backups(self):
        gui = SimpleNamespace(
            _save_timer=_StubTimer(),
            handle_remove_readonly=lambda *_args: None,
        )

        with (
            patch.object(app_module, "delete_all_build_folders", return_value=[]),
            patch.object(app_module, "delete_session_artifacts") as delete_artifacts,
        ):
            success, failed = DIMPackageGUI.performSessionCleanup(gui)

        self.assertTrue(success)
        self.assertEqual(failed, [])
        delete_artifacts.assert_called_once_with(
            app_module.SESSION_FILE,
            include_backups=True,
        )

    def test_exit_dialog_can_be_constructed(self):
        parent = QWidget()
        dialog = ExitDialog(parent)
        self.assertEqual(dialog.getResult(), ExitDialog.RESULT_CANCEL)
        dialog.deleteLater()
        parent.deleteLater()
        self.app.processEvents()

    def test_late_planning_result_is_discarded_while_closing(self):
        plan = SimpleNamespace(cleaned=0)
        plan.cleanup = lambda: setattr(plan, "cleaned", plan.cleaned + 1)
        gui = SimpleNamespace(
            operation_state=OperationState.CLOSING,
            _archive_import_plan=plan,
        )
        result = SimpleNamespace(plan=plan, succeeded=True)

        DIMPackageGUI._onArchivePlanningResult(gui, result)

        self.assertEqual(plan.cleaned, 1)
        self.assertIsNone(gui._archive_import_plan)

    def test_planner_finish_does_not_unlock_active_import(self):
        worker = object()
        state_changes = []
        gui = SimpleNamespace(
            archivePlanningWorker=worker,
            operation_state=OperationState.EXTRACTING,
            _archive_import_plan=object(),
            _setOperationState=state_changes.append,
        )

        DIMPackageGUI._onPlanningWorkerFinished(gui, worker)

        self.assertIsNone(gui.archivePlanningWorker)
        self.assertEqual(state_changes, [])

    def test_clear_fields_emits_cover_reset(self):
        calls = []
        gui = SimpleNamespace(
            product_name_input=SimpleNamespace(clear=lambda: calls.append("name")),
            sku_input=SimpleNamespace(clear=lambda: calls.append("sku")),
            product_part_input=SimpleNamespace(setValue=lambda value: calls.append(("part", value))),
            generateGUID=lambda: calls.append("guid"),
            support_clean_input=SimpleNamespace(setChecked=lambda value: calls.append(("clean", value))),
            cleanUpTemporaryImage=lambda: calls.append("abort"),
            image_label=SimpleNamespace(resetToPlaceholder=lambda: calls.append("cover-reset")),
            updateZipPreview=lambda: calls.append("preview"),
        )

        with patch.object(app_module, "show_info"):
            DIMPackageGUI.clearFields(gui)

        self.assertIn("cover-reset", calls)

    def test_config_reload_preserves_current_session_store_without_signals(self):
        class Combo:
            def __init__(self):
                self.items = ["Custom Store"]
                self.text = "Custom Store"
                self.blocked = False
                self.emissions = 0

            def currentText(self):
                return self.text

            def blockSignals(self, blocked):
                previous = self.blocked
                self.blocked = blocked
                return previous

            def clear(self):
                self.items = []
                self.text = ""
                if not self.blocked:
                    self.emissions += 1

            def addItems(self, items):
                self.items.extend(items)
                if self.items:
                    self.text = self.items[0]
                if not self.blocked:
                    self.emissions += 1

            def findText(self, text):
                return self.items.index(text) if text in self.items else -1

            def setCurrentIndex(self, index):
                self.text = self.items[index] if index >= 0 else ""

            def setCurrentText(self, text):
                self.text = text

            def setCompleter(self, _completer):
                pass

        combo = Combo()
        gui = SimpleNamespace(store_input=combo, doc_main_dir="unused")
        with (
            patch.object(
                app_module,
                "load_configurations",
                return_value=(
                    ["DAZ 3D", "Renderosity"],
                    {"DAZ 3D": "IM", "Renderosity": "RO"},
                    ["General"],
                    ["Runtime"],
                ),
            ),
            patch.object(app_module, "QCompleter", return_value=object()),
        ):
            DIMPackageGUI._reloadConfigurationChoices(gui)

        self.assertEqual(combo.currentText(), "Custom Store")
        self.assertEqual(combo.emissions, 0)


class CoverPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_local_cover_is_normalized_and_remove_emits_empty_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            covers = root / "Covers"
            source = root / "source.png"
            image = QImage(20, 10, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.red)
            self.assertTrue(image.save(str(source), "PNG"))

            with patch.object(widgets_module, "COVERS_DIR", str(covers)):
                label = ImageLabel()
                changes = []
                label.imageChanged.connect(changes.append)

                self.assertTrue(label._adopt_local_as_temp(str(source)))
                managed = Path(label.imagePath)
                self.assertEqual(managed.parent, covers)
                self.assertEqual(managed.suffix.casefold(), ".jpg")
                self.assertTrue(managed.is_file())
                self.assertEqual(changes[-1], str(managed))

                label.removeImage()
                self.assertEqual(label.imagePath, "")
                self.assertEqual(changes[-1], "")
                label.deleteLater()
                self.app.processEvents()

    def test_failed_replacement_preserves_existing_cover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            covers = root / "Covers"
            source = root / "source.png"
            invalid = root / "invalid.img"
            image = QImage(10, 10, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.blue)
            self.assertTrue(image.save(str(source), "PNG"))
            invalid.write_bytes(b"not an image")

            with patch.object(widgets_module, "COVERS_DIR", str(covers)):
                label = ImageLabel()
                self.assertTrue(label._adopt_local_as_temp(str(source)))
                managed = label.imagePath
                changes = []
                label.imageChanged.connect(changes.append)

                self.assertFalse(label._adopt_local_as_temp(str(invalid)))
                self.assertEqual(label.imagePath, managed)
                self.assertEqual(changes, [])

                label.MAX_IMAGE_PIXELS = 4
                oversized = QImage(3, 3, QImage.Format.Format_RGB32)
                self.assertFalse(label._adopt_qimage_as_temp(oversized))
                self.assertEqual(label.imagePath, managed)
                label.deleteLater()
                self.app.processEvents()

    def test_aborting_previous_download_invalidates_its_result(self):
        class Reply:
            def __init__(self):
                self.aborted = False

            def isFinished(self):
                return False

            def abort(self):
                self.aborted = True

        label = ImageLabel()
        reply = Reply()
        label._active_reply = reply
        before = label._load_seq

        label._abort_active_download()

        self.assertTrue(reply.aborted)
        self.assertIsNone(label._active_reply)
        self.assertEqual(label._load_seq, before + 1)
        self.assertEqual(label.MAX_IMAGE_BYTES, 20 * 1024 * 1024)
        self.assertEqual(label.MAX_IMAGE_PIXELS, 40_000_000)
        self.assertEqual(label.DOWNLOAD_TIMEOUT_MS, 15_000)
        label.deleteLater()
        self.app.processEvents()

    def test_remove_aborts_an_active_download(self):
        class Reply:
            def __init__(self):
                self.aborted = False

            def isFinished(self):
                return False

            def abort(self):
                self.aborted = True

        label = ImageLabel()
        reply = Reply()
        label._active_reply = reply
        before = label._load_seq

        label.removeImage()

        self.assertTrue(reply.aborted)
        self.assertIsNone(label._active_reply)
        self.assertEqual(label._load_seq, before + 1)
        label.deleteLater()
        self.app.processEvents()


class ExplorerContainmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_explorer_rejects_a_broad_fallback_root(self):
        explorer = FileExplorer("")
        self.assertFalse(explorer._root_valid)
        self.assertEqual(explorer.current_path, "")
        self.assertTrue(explorer.treeView.isHidden())
        explorer.deleteLater()
        self.app.processEvents()

    def test_explorer_requires_an_active_build_for_a_nonempty_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            explorer = FileExplorer(temp_dir)
            self.assertFalse(explorer._root_valid)
            self.assertTrue(explorer.treeView.isHidden())
            explorer.deleteLater()
            self.app.processEvents()

    def test_mutation_is_blocked_when_root_does_not_match_current_build(self):
        notifications = []
        explorer = SimpleNamespace(
            main_gui=SimpleNamespace(
                canMutateWorkspace=lambda: True,
                current_build=SimpleNamespace(folder="Build1"),
            ),
            current_path="C:\\stale\\Content",
            _root_valid=True,
            isEnabled=lambda: True,
            InvalidFolderInfoBar=lambda: notifications.append("blocked"),
        )

        with patch.object(
            widgets_module,
            "get_build_content_dir",
            return_value="C:\\current\\Content",
        ):
            self.assertFalse(FileExplorer._mutation_allowed(explorer))

        self.assertEqual(notifications, ["blocked"])

    def test_explorer_refuses_to_display_another_build_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            expected = base / "Build001" / "Content"
            other = base / "Build002" / "Content"
            expected.mkdir(parents=True)
            other.mkdir(parents=True)
            main_gui = SimpleNamespace(
                current_build=SimpleNamespace(folder="Build001")
            )

            with patch.object(
                widgets_module, "get_build_content_dir", return_value=str(expected)
            ):
                explorer = FileExplorer(str(expected), main_gui=main_gui)
                self.assertTrue(explorer._root_valid)
                self.assertFalse(explorer.setRootPath(str(other)))
                self.assertTrue(explorer.treeView.isHidden())
                explorer.deleteLater()
                self.app.processEvents()

    def test_explorer_rejects_reparse_ancestors_of_content_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            expected = Path(temp_dir) / "Build001" / "Content"
            expected.mkdir(parents=True)
            explorer = SimpleNamespace(
                main_gui=SimpleNamespace(
                    current_build=SimpleNamespace(folder="Build001")
                )
            )

            with (
                patch.object(
                    widgets_module,
                    "get_build_content_dir",
                    return_value=str(expected),
                ),
                patch.object(
                    widgets_module, "has_reparse_component", return_value=True
                ),
            ):
                self.assertFalse(
                    FileExplorer._root_is_safe(explorer, str(expected))
                )

    def test_windows_component_validation_rejects_escape_and_reserved_names(self):
        invalid = (
            "",
            ".",
            "..",
            "CON",
            "CON .duf",
            "CLOCK$.txt",
            "COM¹.asset",
            "nul.txt",
            "name:stream",
            "folder/name",
            "name.",
            " leading",
            "x" * 256,
        )
        for name in invalid:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_windows_name(name)
        self.assertEqual(validate_windows_name("合法 Name_1.duf"), "合法 Name_1.duf")

    def test_reparse_component_blocks_child_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Content"
            linked = root / "linked"
            linked.mkdir(parents=True)
            child = linked / "child"
            child.mkdir()

            with patch(
                "utils.has_reparse_point",
                side_effect=lambda path: Path(path).name == "linked",
            ):
                self.assertTrue(has_reparse_component(str(child), str(root)))
                with self.assertRaises(ValueError):
                    safe_child_path(str(root), str(child), "file.duf")

    def test_checked_path_protects_root_and_outside_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Content"
            inside = root / "Runtime" / "file.duf"
            outside = Path(temp_dir) / "outside.duf"
            inside.parent.mkdir(parents=True)
            inside.write_text("inside", encoding="utf-8")
            outside.write_text("outside", encoding="utf-8")
            explorer = SimpleNamespace(current_path=str(root))

            self.assertEqual(
                FileExplorer._checked_path(explorer, str(inside)), str(inside)
            )
            with self.assertRaises(ValueError):
                FileExplorer._checked_path(explorer, str(root))
            with self.assertRaises(ValueError):
                FileExplorer._checked_path(explorer, str(outside))

    def test_failed_overwrite_restores_original_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "Content"
            root.mkdir()
            source = base / "source.txt"
            target = root / source.name
            source.write_text("new", encoding="utf-8")
            target.write_text("old", encoding="utf-8")

            class Harness:
                current_path = str(root)

                @staticmethod
                def _mutation_allowed():
                    return True

                def _checked_path(self, path, **kwargs):
                    return FileExplorer._checked_path(self, path, **kwargs)

                @staticmethod
                def _validate_copy_source(path):
                    return FileExplorer._validate_copy_source(path)

                @staticmethod
                def _remove_permanently(path):
                    return FileExplorer._remove_permanently(path)

            real_replace = os.replace

            def fail_publish(source_path, destination_path):
                if str(source_path).endswith(".stage") and Path(destination_path) == target:
                    raise OSError("simulated publish failure")
                return real_replace(source_path, destination_path)

            with (
                patch.object(
                    widgets_module.QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch.object(widgets_module.os, "replace", side_effect=fail_publish),
                patch.object(widgets_module, "show_error"),
            ):
                result = FileExplorer._transfer_path(
                    Harness(), str(source), str(root), move=False
                )

            self.assertFalse(result)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(
                [item for item in root.iterdir() if item.name.startswith(".dimcreator-")],
                [],
            )

    def test_failed_explorer_backup_cleanup_stays_outside_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "Content"
            root.mkdir()
            source = base / "source.txt"
            target = root / source.name
            source.write_text("new", encoding="utf-8")
            target.write_text("old", encoding="utf-8")

            class Harness:
                current_path = str(root)

                @staticmethod
                def _mutation_allowed():
                    return True

                def _checked_path(self, path, **kwargs):
                    return FileExplorer._checked_path(self, path, **kwargs)

                @staticmethod
                def _validate_copy_source(path):
                    return FileExplorer._validate_copy_source(path)

                @staticmethod
                def _remove_permanently(path):
                    if str(path).endswith(".backup"):
                        raise PermissionError("simulated cleanup failure")
                    return FileExplorer._remove_permanently(path)

            with (
                patch.object(
                    widgets_module.QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch.object(widgets_module, "show_error"),
            ):
                result = FileExplorer._transfer_path(
                    Harness(), str(source), str(root), move=False
                )

            self.assertTrue(result)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertFalse(any(root.glob(".dimcreator-*")))
            backups = list(base.glob(".dimcreator-fileop-*/*.backup"))
            self.assertEqual(len(backups), 1)

    def test_trash_adapter_uses_the_qt_tuple_status(self):
        with patch("utils.QFile.moveToTrash", return_value=(False, "")):
            self.assertFalse(move_to_trash("C:\\Content\\file.duf"))
        with patch("utils.QFile.moveToTrash", side_effect=RuntimeError("failed")):
            self.assertFalse(move_to_trash("C:\\Content\\file.duf"))
        with patch(
            "utils.QFile.moveToTrash",
            return_value=(True, "C:\\$Recycle.Bin\\file.duf"),
        ):
            self.assertTrue(move_to_trash("C:\\Content\\file.duf"))


if __name__ == "__main__":
    unittest.main()
