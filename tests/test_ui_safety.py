import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QObject, QTimer, Qt, QUrl, Slot
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
    plan_archive_import,
)
from operation_state import OperationState
from session import Build, SessionLoadResult, create_default_session
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
        self.started = 0
        self.active = False

    def stop(self):
        self.stopped += 1
        self.active = False

    def start(self):
        self.started += 1
        self.active = True

    def isActive(self):
        return self.active


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
        gui = SimpleNamespace(
            _close_ready=False,
            _runningWorkers=lambda: [],
            hasUserMadeChanges=lambda: False,
            _setOperationState=states.append,
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

    def test_extraction_result_adds_new_build_before_single_revalidation(self):
        session = create_default_session()
        new_build = Build(
            id="build_002",
            folder="Build002",
            part=2,
            guid="00000000-0000-0000-0000-000000000002",
        )
        calls = []
        transaction = SimpleNamespace(
            finalize=lambda: calls.append("finalize"),
            rollback=lambda: calls.append("rollback"),
            rollback_pending=False,
        )
        result = ExtractionResult(
            "success",
            modified_builds=["Build002"],
            new_builds=[new_build.to_dict()],
            next_build_number=3,
            _transaction=transaction,
        )
        gui = SimpleNamespace(
            session=session,
            current_build=session.builds[0],
            _revalidateAllBuildsStatus=lambda: calls.append(
                ("revalidate", len(gui.session.builds))
            ),
            buildListWidget=SimpleNamespace(
                setSession=lambda value: calls.append(("set-session", value)),
                refreshList=lambda: calls.append("refresh-list"),
            ),
            fileExplorer=SimpleNamespace(
                refresh_view=lambda: calls.append("refresh-files")
            ),
            saveSession=lambda: calls.append("save") or True,
            showExtractionState=lambda *_args, **_kwargs: None,
            _extractionSuccessMessage=lambda value: (
                DIMPackageGUI._extractionSuccessMessage(value)
            ),
        )

        with (patch.object(app_module, "show_success"),
              patch.object(app_module, "show_info")):
            DIMPackageGUI._onExtractionResult(gui, result)

        self.assertEqual([build.id for build in gui.session.builds], [
            "build_001", "build_002",
        ])
        self.assertEqual(gui.session.next_build_number, 3)
        self.assertEqual(calls.count(("revalidate", 2)), 1)
        self.assertLess(calls.index(("revalidate", 2)), calls.index("save"))
        self.assertIn("finalize", calls)
        self.assertNotIn("rollback", calls)

    def test_extraction_session_save_failure_restores_session_and_files(self):
        session = create_default_session()
        original_build_id = session.builds[0].id
        new_build = Build(
            id="build_002",
            folder="Build002",
            part=2,
            guid="00000000-0000-0000-0000-000000000002",
        )
        calls = []
        transaction = SimpleNamespace(
            finalize=lambda: calls.append("finalize"),
            rollback=lambda: calls.append("rollback"),
            rollback_pending=True,
        )
        result = ExtractionResult(
            "success",
            new_builds=[new_build.to_dict()],
            next_build_number=3,
            _transaction=transaction,
        )

        class BuildList:
            def setSession(self, value):
                calls.append(("set-session", len(value.builds)))

            def refreshList(self):
                calls.append("refresh-list")

            def blockSignals(self, blocked):
                calls.append(("block", blocked))
                return False

            def selectBuild(self, build_id):
                calls.append(("select", build_id))

        gui = SimpleNamespace(
            session=session,
            current_build=session.builds[0],
            _revalidateAllBuildsStatus=lambda: calls.append("revalidate"),
            buildListWidget=BuildList(),
            fileExplorer=SimpleNamespace(
                setRootPath=lambda path: calls.append(("root", path)),
                refresh_view=lambda: calls.append("refresh-files"),
            ),
            saveSession=lambda: False,
            loadBuildIntoEditor=lambda build: calls.append(("load", build.id)),
            showExtractionState=lambda *_args, **_kwargs: None,
            _retryExtractionRollback=lambda value: None,
        )

        with (
            patch.object(app_module, "get_build_content_dir", return_value="content"),
            patch.object(app_module, "show_error"),
        ):
            DIMPackageGUI._onExtractionResult(gui, result)

        self.assertEqual([build.id for build in gui.session.builds], [original_build_id])
        self.assertEqual(gui.current_build.id, original_build_id)
        self.assertEqual(calls.count("rollback"), 1)
        self.assertNotIn("finalize", calls)
        self.assertEqual(result.status, "error")

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

            with patch.object(extraction_module, "MIN_FREE_SPACE_BYTES", 0):
                plan = plan_archive_import(str(archive), {"Runtime"}, True)
            worker = ContentExtractionWorker(
                plan,
                str(content),
                str(templates),
                prompt_on_conflicts=True,
            )
            results = []
            worker.resultReady.connect(results.append)

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
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "success")
            self.assertEqual(results[0].modified_builds, [])
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

    def test_build_selection_persists_the_build_id_directly(self):
        first = SimpleNamespace(id="build_001", folder="Build001")
        second = SimpleNamespace(id="build_002", folder="Build002")
        session = SimpleNamespace(
            builds=[first, second],
            last_selected_build_id=first.id,
        )
        roots = []
        loaded = []
        gui = SimpleNamespace(
            session=session,
            current_build=first,
            canMutateWorkspace=lambda: True,
            loadBuildIntoEditor=loaded.append,
            fileExplorer=SimpleNamespace(setRootPath=roots.append),
            saveSession=lambda: True,
        )

        DIMPackageGUI.onBuildSelected(gui, second.id)

        self.assertIs(gui.current_build, second)
        self.assertEqual(session.last_selected_build_id, second.id)
        self.assertEqual(loaded, [second])
        self.assertEqual(len(roots), 1)


class SessionPreferenceAndGuidTests(unittest.TestCase):
    class MemorySettings:
        def __init__(self, values=None):
            self.values = dict(values or {})
            self.syncs = 0

        def value(self, key, default=None, type=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

        def sync(self):
            self.syncs += 1

    def test_store_preference_uses_configured_value_or_first_store(self):
        gui = SimpleNamespace(storeitems=["DAZ 3D", "Renderosity"])

        for saved, expected in (
            ("renderosity", "Renderosity"),
            ("Removed Store", "DAZ 3D"),
            ("", "DAZ 3D"),
        ):
            with self.subTest(saved=saved), patch.object(
                app_module,
                "settings",
                self.MemorySettings({"store_input": saved}),
            ):
                self.assertEqual(
                    DIMPackageGUI._preferredStoreForNewSession(gui),
                    expected,
                )

    def test_prefix_preference_uses_valid_value_or_local(self):
        gui = SimpleNamespace()

        for saved, expected in (
            ("rdna", "RDNA"),
            ("invalid-prefix", "LOCAL"),
            ("", "LOCAL"),
        ):
            with self.subTest(saved=saved), patch.object(
                app_module,
                "settings",
                self.MemorySettings({"prefix_input": saved}),
            ):
                self.assertEqual(
                    DIMPackageGUI._preferredPrefixForNewSession(gui),
                    expected,
                )

    def test_save_settings_keeps_the_last_nonempty_store_and_valid_prefix(self):
        memory = self.MemorySettings(
            {"store_input": "DAZ 3D", "prefix_input": "OLD"}
        )
        gui = SimpleNamespace(
            store_input=SimpleNamespace(currentText=lambda: "Renderosity"),
            prefix_input=SimpleNamespace(text=lambda: "rdna"),
            last_destination_folder=r"C:\Packages",
            use_store_prefix_checkbox=SimpleNamespace(isChecked=lambda: True),
        )

        with patch.object(app_module, "settings", memory):
            DIMPackageGUI.saveSettings(gui)
            gui.store_input = SimpleNamespace(currentText=lambda: "   ")
            gui.prefix_input = SimpleNamespace(text=lambda: "invalid-prefix")
            DIMPackageGUI.saveSettings(gui)

        self.assertEqual(memory.values["store_input"], "Renderosity")
        self.assertEqual(memory.values["prefix_input"], "RDNA")
        self.assertEqual(memory.syncs, 2)

    def test_new_session_uses_preferred_store_without_replacing_its_guid(self):
        session = create_default_session()
        original_guid = session.builds[0].guid
        saves = []
        gui = SimpleNamespace(
            _session_warning="",
            session=None,
            current_build=None,
            _preferredStoreForNewSession=lambda: "Renderosity",
            _preferredPrefixForNewSession=lambda: "RDNA",
            saveSession=lambda: saves.append(True) or True,
        )

        with (
            patch.object(
                app_module,
                "load_session_result",
                return_value=SessionLoadResult(None, "new"),
            ),
            patch.object(app_module, "create_default_session", return_value=session),
            patch.object(app_module, "create_build_folder"),
        ):
            DIMPackageGUI.loadSession(gui)

        self.assertEqual(session.builds[0].store, "Renderosity")
        self.assertEqual(session.builds[0].prefix, "RDNA")
        self.assertEqual(session.builds[0].guid, original_guid)
        self.assertEqual(saves, [True])

    def test_loaded_session_store_is_not_replaced_by_global_preference(self):
        session = create_default_session()
        session.builds[0].store = "DAZ 3D"
        gui = SimpleNamespace(
            _session_warning="",
            session=None,
            current_build=None,
            _preferredStoreForNewSession=lambda: self.fail(
                "loaded sessions must not consult the global store preference"
            ),
            _preferredPrefixForNewSession=lambda: self.fail(
                "loaded sessions must not consult the global prefix preference"
            ),
            _isPristineSession=lambda: False,
            _revalidateAllBuildsStatus=lambda: None,
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                app_module,
                "load_session_result",
                return_value=SessionLoadResult(session, "primary"),
            ),
            patch.object(
                app_module,
                "get_build_content_dir",
                return_value=temp_dir,
            ),
        ):
            DIMPackageGUI.loadSession(gui)

        self.assertEqual(session.builds[0].store, "DAZ 3D")

    def test_pristine_session_detection_is_conservative(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content = Path(temp_dir)
            session = create_default_session()
            gui = SimpleNamespace(session=session)

            with patch.object(
                app_module, "get_build_content_dir", return_value=str(content)
            ):
                (content / "thumbs.DB").write_bytes(b"")
                self.assertTrue(DIMPackageGUI._isPristineSession(gui))

                (content / "product.duf").write_bytes(b"content")
                self.assertFalse(DIMPackageGUI._isPristineSession(gui))
                (content / "product.duf").unlink()

                session.builds[0].product_name = "Product"
                self.assertFalse(DIMPackageGUI._isPristineSession(gui))
                session.builds[0].product_name = ""

                session.builds.append(
                    Build(
                        id="build_002",
                        folder="Build002",
                        part=2,
                        guid=str(app_module.uuid.uuid4()),
                    )
                )
                self.assertFalse(DIMPackageGUI._isPristineSession(gui))
                session.builds.pop()

                with patch.object(app_module.os, "listdir", side_effect=OSError("locked")):
                    self.assertFalse(DIMPackageGUI._isPristineSession(gui))

    def test_loaded_pristine_session_gets_a_new_guid_without_immediate_save(self):
        session = create_default_session()
        original_guid = session.builds[0].guid
        replacement = app_module.uuid.UUID("12345678-1234-4234-8234-123456789abc")
        saves = []
        gui = SimpleNamespace(
            _session_warning="",
            session=None,
            current_build=None,
            _revalidateAllBuildsStatus=lambda: None,
            saveSession=lambda: saves.append(True) or True,
        )
        gui._isPristineSession = lambda: DIMPackageGUI._isPristineSession(gui)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                app_module,
                "load_session_result",
                return_value=SessionLoadResult(session, "primary"),
            ),
            patch.object(
                app_module,
                "get_build_content_dir",
                return_value=temp_dir,
            ),
            patch.object(app_module.uuid, "uuid4", return_value=replacement),
        ):
            DIMPackageGUI.loadSession(gui)

        self.assertNotEqual(session.builds[0].guid, original_guid)
        self.assertEqual(session.builds[0].guid, str(replacement))
        self.assertEqual(saves, [])

    def test_guid_edit_only_commits_complete_values(self):
        session = create_default_session()
        build = session.builds[0]
        original_guid = build.guid
        saves = []
        gui = SimpleNamespace(
            _loading_build=False,
            current_build=build,
            session=session,
            canMutateWorkspace=lambda: True,
            saveBuildFieldChanges=lambda: saves.append(True),
        )

        DIMPackageGUI.onGuidChanged(gui, "")
        DIMPackageGUI.onGuidChanged(gui, "12345678-1234")
        self.assertEqual(build.guid, original_guid)
        self.assertEqual(saves, [])

        replacement = "12345678-1234-4234-8234-123456789ABC"
        DIMPackageGUI.onGuidChanged(gui, replacement)
        self.assertEqual(build.guid, replacement)
        self.assertEqual(saves, [True])

    def test_other_field_changes_ignore_an_incomplete_visible_guid(self):
        session = create_default_session()
        build = session.builds[0]
        original_guid = build.guid
        timer = _StubTimer()
        gui = SimpleNamespace(
            _loading_build=False,
            current_build=build,
            session=session,
            canMutateWorkspace=lambda: True,
            store_input=SimpleNamespace(currentText=lambda: "DAZ 3D"),
            product_name_input=SimpleNamespace(text=lambda: "Product"),
            prefix_input=SimpleNamespace(text=lambda: "LOCAL"),
            sku_input=SimpleNamespace(text=lambda: "123"),
            product_tags_input=SimpleNamespace(text=lambda: "DAZStudio4_5"),
            guid_input=SimpleNamespace(text=lambda: "12345678-1234"),
            image_label=SimpleNamespace(imagePath=""),
            daz_folders=["Runtime"],
            _save_timer=timer,
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                app_module, "get_build_content_dir", return_value=temp_dir
            ),
            patch.object(app_module, "validate_build", return_value="incomplete"),
        ):
            DIMPackageGUI.saveBuildFieldChanges(gui)
            saved_path = Path(temp_dir) / "session.json"
            app_module.save_session(session, saved_path)
            loaded = app_module.load_session_result(saved_path).session

        self.assertEqual(build.product_name, "Product")
        self.assertEqual(build.guid, original_guid)
        self.assertEqual(loaded.builds[0].guid, original_guid)
        self.assertEqual(timer.started, 1)

    def test_packaging_validates_the_visible_guid_for_the_active_build(self):
        session = create_default_session()
        build = session.builds[0]
        build.store = "DAZ 3D"
        build.product_name = "Product"
        build.prefix = "LOCAL"
        build.sku = "123"
        gui = SimpleNamespace(
            session=session,
            current_build=build,
            guid_input=SimpleNamespace(text=lambda: "12345678-1234"),
            support_clean_input=SimpleNamespace(isChecked=lambda: False),
            daz_folders=["Runtime"],
        )
        inventory = SimpleNamespace(
            manifest_members=("Content/Runtime/product.duf",)
        )

        with patch.object(
            app_module.PackageInventory, "from_content", return_value=inventory
        ):
            result = DIMPackageGUI._validateBuildsForPackaging(gui, [build])

        self.assertIn("Invalid GUID format", result[0]["issues"])


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

    def test_download_size_is_checked_after_metadata_arrives(self):
        class SignalStub:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

            def emit(self, *args):
                for callback in self.callbacks:
                    callback(*args)

        class Reply:
            def __init__(self):
                self.metaDataChanged = SignalStub()
                self.downloadProgress = SignalStub()
                self.finished = SignalStub()
                self.content_length = ImageLabel.MAX_IMAGE_BYTES + 1
                self.header_calls = 0
                self.aborted = False

            def header(self, _header):
                self.header_calls += 1
                return self.content_length

            def abort(self):
                self.aborted = True

            def isFinished(self):
                return False

        reply = Reply()
        label = ImageLabel()
        label._nam = SimpleNamespace(get=lambda _request: reply)

        label._download_first_valid(
            [QUrl("https://example.invalid/cover.jpg")],
            label._load_seq,
        )

        self.assertEqual(reply.header_calls, 0)
        self.assertFalse(reply.aborted)

        reply.metaDataChanged.emit()

        self.assertEqual(reply.header_calls, 1)
        self.assertTrue(reply.aborted)

        for invalid_length in ("invalid", -1):
            with self.subTest(content_length=invalid_length):
                reply.aborted = False
                reply.content_length = invalid_length
                reply.metaDataChanged.emit()
                self.assertTrue(reply.aborted)

        reply.aborted = False
        reply.downloadProgress.emit(label.MAX_IMAGE_BYTES + 1, -1)
        self.assertTrue(reply.aborted)

        label._download_timer.stop()
        label._active_reply = None
        label.deleteLater()
        self.app.processEvents()

    def test_image_byte_buffer_is_unparented_and_closed(self):
        buffer = SimpleNamespace(
            setData=lambda _data: None,
            open=lambda _mode: True,
            close=lambda: setattr(buffer, "closed", True),
            closed=False,
        )
        size = SimpleNamespace(isValid=lambda: True, width=lambda: 1, height=lambda: 1)
        decoded = QImage(1, 1, QImage.Format.Format_RGB32)
        reader = SimpleNamespace(
            setDecideFormatFromContent=lambda _enabled: None,
            setAutoTransform=lambda _enabled: None,
            size=lambda: size,
            read=lambda: decoded,
        )
        label = SimpleNamespace(MAX_IMAGE_BYTES=100, MAX_IMAGE_PIXELS=100)

        with (
            patch.object(widgets_module, "QBuffer", return_value=buffer) as qbuffer,
            patch.object(widgets_module, "QImageReader", return_value=reader),
        ):
            result = ImageLabel._read_image_bytes(label, b"image")

        qbuffer.assert_called_once_with()
        self.assertIs(result, decoded)
        self.assertTrue(buffer.closed)

    def test_percent_encoded_data_url_preserves_binary_bytes(self):
        label = ImageLabel()
        captured = []
        decoded = QImage(1, 1, QImage.Format.Format_RGB32)
        label._read_image_bytes = lambda data: captured.append(data) or decoded
        label._persist_image = lambda _image: "managed.jpg"
        label._show_image = lambda *_args: None

        adopted = label._adopt_data_url(
            QUrl("data:image/png,%89PNG%0D%0A%1A%0A")
        )

        self.assertTrue(adopted)
        self.assertEqual(captured, [b"\x89PNG\r\n\x1a\n"])
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

    def test_folder_creation_error_includes_path_and_os_error(self):
        class Dialog:
            def __init__(self, *_args, **_kwargs):
                pass

            def exec(self):
                return True

            def getName(self):
                return "New Folder"

        with tempfile.TemporaryDirectory() as temp_dir:
            index = SimpleNamespace(isValid=lambda: False)
            explorer = SimpleNamespace(
                _mutation_allowed=lambda: True,
                treeView=SimpleNamespace(currentIndex=lambda: index),
                model=SimpleNamespace(rootPath=lambda: temp_dir),
                current_path=temp_dir,
                _checked_path=lambda path, allow_root: path,
            )

            with (
                patch.object(widgets_module, "NameEntryDialog", Dialog),
                patch.object(
                    widgets_module.os,
                    "mkdir",
                    side_effect=PermissionError("access denied"),
                ),
                patch.object(widgets_module, "show_error") as show_error,
                patch.object(widgets_module.log, "error") as log_error,
            ):
                FileExplorer.createNewFolder(explorer)

            message = show_error.call_args.args[2]
            self.assertIn("New Folder", message)
            self.assertIn("access denied", message)
            self.assertIn("access denied", log_error.call_args.args[0])

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
