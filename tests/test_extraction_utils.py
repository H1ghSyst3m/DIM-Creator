import io
import os
import stat
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from unittest import mock

import extraction_utils as extraction
import utils
from session import Build, Session


def _rar_creator():
    unrar = utils.find_unrar_executable()
    if not unrar:
        return None
    directory = os.path.dirname(unrar)
    return utils._trusted_executable(
        os.path.join(directory, "Rar.exe"), boundary=directory
    )


def _zip_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


def _write_zip(path, entries, compression=zipfile.ZIP_DEFLATED):
    with open(path, "wb") as output:
        output.write(_zip_bytes(entries, compression))


class ArchiveClassificationTests(unittest.TestCase):
    def test_template_detection_requires_a_complete_word(self):
        content, templates, ignored = extraction.classify_archives(
            [
                "Temple Ruins.zip",
                "Product_template.zip",
                "Product Templates.7z",
                "Templateish.rar",
            ],
            True,
        )

        self.assertEqual(content, ["Temple Ruins.zip", "Templateish.rar"])
        self.assertEqual(templates, ["Product_template.zip", "Product Templates.7z"])
        self.assertEqual(ignored, [])

    def test_disabled_template_detection_ignores_only_real_templates(self):
        content, templates, ignored = extraction.classify_archives(
            ["Temple.zip", "Product-Template.zip"], False
        )

        self.assertEqual(content, ["Temple.zip"])
        self.assertEqual(templates, [])
        self.assertEqual(ignored, ["Product-Template.zip"])


class MultipartOrderingTests(unittest.TestCase):
    def test_orders_complete_x_of_y_sequence(self):
        ordered, warning = extraction.detect_heuristic_ordering(
            ["Product_2of2.zip", "Product_1of2.zip"]
        )

        self.assertEqual(ordered, ["Product_1of2.zip", "Product_2of2.zip"])
        self.assertIsNone(warning)

    def test_rejects_missing_multipart_member(self):
        with self.assertRaisesRegex(extraction.MultipartArchiveError, "missing parts"):
            extraction.detect_heuristic_ordering(
                ["Product_1of3.zip", "Product_3of3.zip"]
            )

    def test_rejects_duplicate_multipart_member(self):
        with self.assertRaisesRegex(extraction.MultipartArchiveError, "Duplicate"):
            extraction.detect_heuristic_ordering(
                ["Product_1of2.zip", "Copy_1of2.zip"]
            )

    def test_rejects_mixed_numbered_and_unnumbered_members(self):
        with self.assertRaisesRegex(
            extraction.MultipartArchiveError, "Mixed or incomplete"
        ):
            extraction.detect_heuristic_ordering(
                ["Product_1of3.zip", "Product_other.zip"]
            )

    def test_rejects_single_member_declaring_multiple_parts(self):
        with self.assertRaisesRegex(extraction.MultipartArchiveError, "missing parts"):
            extraction.detect_heuristic_ordering(["Product_2of2.zip"])


class SafeArchiveInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dim_extract_test_")
        self.addCleanup(self.temp.cleanup)

    def _archive(self, name, entries):
        path = os.path.join(self.temp.name, name)
        _write_zip(path, entries)
        return path

    def test_rejects_unsafe_windows_and_traversal_paths(self):
        unsafe_paths = (
            "../escape.txt",
            "/absolute.txt",
            "C:/drive.txt",
            "Runtime/file.txt:stream",
            "Runtime/CON.txt",
            "Runtime/trailing.",
        )
        for index, member in enumerate(unsafe_paths):
            with self.subTest(member=member):
                archive = self._archive(f"unsafe_{index}.zip", {member: b"x"})
                with self.assertRaises(extraction.UnsafeArchiveError):
                    extraction.inspect_archive(archive)

    def test_rejects_case_insensitive_member_collision(self):
        archive = self._archive(
            "collision.zip",
            {"Runtime/Test.txt": b"one", "runtime/test.TXT": b"two"},
        )

        with self.assertRaisesRegex(extraction.UnsafeArchiveError, "collision"):
            extraction.inspect_archive(archive)

    def test_rejects_case_insensitive_directory_prefix_collision(self):
        archive = self._archive(
            "directory_collision.zip",
            {"Foo/one.txt": b"one", "foo/two.txt": b"two"},
        )

        with self.assertRaisesRegex(extraction.UnsafeArchiveError, "collision"):
            extraction.inspect_archive(archive)

    def test_rejects_file_used_as_a_parent_directory(self):
        archive = self._archive(
            "prefix_collision.zip",
            {"Runtime/blocked": b"file", "Runtime/blocked/child.txt": b"child"},
        )

        with self.assertRaisesRegex(extraction.UnsafeArchiveError, "parent directory"):
            extraction.inspect_archive(archive)

    def test_rejects_zip_symlink(self):
        archive = os.path.join(self.temp.name, "symlink.zip")
        link = zipfile.ZipInfo("Runtime/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(link, "../../outside")

        with self.assertRaisesRegex(extraction.UnsafeArchiveError, "symbolic link"):
            extraction.inspect_archive(archive)

    def test_rejects_suspicious_compression_ratio(self):
        archive = self._archive("bomb.zip", {"Runtime/zeros.bin": b"0" * 4096})

        with (
            mock.patch.object(extraction, "SUSPICIOUS_RATIO_MIN_BYTES", 1),
            mock.patch.object(extraction, "SUSPICIOUS_RATIO", 2),
        ):
            with self.assertRaisesRegex(extraction.UnsafeArchiveError, "compression ratio"):
                extraction.inspect_archive(archive)

    def test_rejects_suspicious_aggregate_compression_ratio(self):
        half_gib = 512 * 1024**2
        members = [
            extraction.ArchiveMember("Runtime/one.bin", half_gib, 1024),
            extraction.ArchiveMember("Runtime/two.bin", half_gib, 1024),
        ]

        with self.assertRaisesRegex(
            extraction.UnsafeArchiveError, "aggregate compression ratio"
        ):
            extraction._validate_inventory("split-bomb.zip", members)

    def test_inventory_validation_is_cooperatively_cancellable(self):
        members = [extraction.ArchiveMember("Runtime/file.txt", 1, 1)]
        with self.assertRaises(extraction.ExtractionCancelled):
            extraction._validate_inventory(
                "cancel.zip", members, cancel_check=lambda: True
            )

    def test_midstream_zip_cancellation_keeps_cancelled_status(self):
        archive = self._archive(
            "cancel.zip", {"Runtime/large.bin": b"x" * (2 * 1024 * 1024)}
        )
        destination = os.path.join(self.temp.name, "cancelled")
        checks = 0

        def cancel_after_member_starts():
            nonlocal checks
            checks += 1
            return checks >= 2

        with extraction._ZipAdapter(archive) as adapter:
            inventory = adapter.inventory()
            with self.assertRaises(extraction.ExtractionCancelled):
                adapter.extract(
                    destination, inventory, cancel_after_member_starts
                )

    def test_copy_rejects_source_replaced_during_secure_open(self):
        source = os.path.join(self.temp.name, "source.zip")
        replacement = os.path.join(self.temp.name, "replacement.zip")
        displaced = os.path.join(self.temp.name, "displaced.zip")
        destination = os.path.join(self.temp.name, "copy.zip")
        with open(source, "wb") as output:
            output.write(b"original")
        with open(replacement, "wb") as output:
            output.write(b"replacement")
        real_open = extraction.os.open
        swapped = False

        def swap_before_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if os.path.abspath(path) == os.path.abspath(source) and not swapped:
                swapped = True
                os.replace(source, displaced)
                os.replace(replacement, source)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(
            extraction.os,
            "open",
            side_effect=swap_before_open,
        ):
            with self.assertRaisesRegex(
                extraction.UnsafeArchiveError,
                "changed while being opened",
            ):
                extraction._copy_file(source, destination, lambda: False)

        self.assertFalse(os.path.exists(destination))

    def test_snapshot_rejects_swap_after_initial_validation(self):
        source = os.path.join(self.temp.name, "source.zip")
        replacement = os.path.join(self.temp.name, "replacement.zip")
        displaced = os.path.join(self.temp.name, "displaced.zip")
        snapshot_root = os.path.join(self.temp.name, "snapshot")
        _write_zip(source, {"Runtime/original.txt": b"original"})
        _write_zip(replacement, {"Runtime/replacement.txt": b"replacement"})
        real_copy = extraction._copy_file

        def swap_before_copy(
            source_path,
            destination,
            cancel_check,
            *,
            expected_stat=None,
        ):
            os.replace(source_path, displaced)
            os.replace(replacement, source_path)
            return real_copy(
                source_path,
                destination,
                cancel_check,
                expected_stat=expected_stat,
            )

        with mock.patch.object(
            extraction,
            "_copy_file",
            side_effect=swap_before_copy,
        ):
            with self.assertRaisesRegex(
                extraction.UnsafeArchiveError,
                "changed before it could be copied",
            ):
                extraction._snapshot_archive(
                    source,
                    snapshot_root,
                    lambda: False,
                )

        self.assertFalse(os.path.exists(os.path.join(snapshot_root, "source.zip")))

    def test_rejects_cumulative_entry_limit(self):
        members = [
            extraction.ArchiveMember(f"Runtime/{index}.txt", 1, 1)
            for index in range(3)
        ]

        with mock.patch.object(extraction, "MAX_ARCHIVE_ENTRIES", 2):
            with self.assertRaisesRegex(extraction.UnsafeArchiveError, "entry limit"):
                extraction._validate_inventory("test.zip", members)

    def test_7z_inventory_parser_preserves_sizes_and_directories(self):
        records = extraction._parse_slt_records(
            "Path = Runtime\\file.txt\nSize = 12\nPacked Size = 8\nFolder = -\n\n"
            "Path = Runtime\\empty\nSize = 0\nPacked Size = 0\nFolder = +\n"
        )

        members = extraction._ExternalAdapter._members_from_7z(records)
        inventory = extraction._validate_inventory("product.7z", members)

        self.assertEqual(inventory.members[0].path, "Runtime/file.txt")
        self.assertEqual(inventory.members[0].size, 12)
        self.assertTrue(inventory.members[1].is_dir)

    def test_external_inventory_parser_stops_near_the_entry_limit(self):
        output = "\n\n".join(
            f"Path = Runtime/{index}.txt\nSize = 1\nPacked Size = 1"
            for index in range(20)
        )

        with mock.patch.object(extraction, "MAX_ARCHIVE_ENTRIES", 2):
            with self.assertRaisesRegex(
                extraction.UnsafeArchiveError, "entry limit"
            ):
                extraction._parse_slt_records(output)

    def test_external_inventory_parser_rejects_an_excessive_line(self):
        with mock.patch.object(extraction, "MAX_EXTERNAL_LINE_CHARS", 8):
            with self.assertRaisesRegex(
                extraction.UnsafeArchiveError, "excessive line"
            ):
                extraction._parse_slt_records("Path = Runtime/too-long.txt")

    def test_7z_inventory_rejects_links_before_extraction(self):
        records = extraction._parse_slt_records(
            "Path = Runtime/link\nSize = 5\nPacked Size = 5\nFolder = -\n"
            "Symbolic Link = ../../outside\n"
        )

        with self.assertRaisesRegex(extraction.UnsafeArchiveError, "link"):
            extraction._ExternalAdapter._members_from_7z(records)

    def test_missing_external_tool_has_actionable_error(self):
        with (
            mock.patch.object(extraction, "find_7z_executable", return_value=None),
            mock.patch.object(extraction, "find_unrar_executable", return_value=None),
        ):
            with self.assertRaisesRegex(extraction.ArchiveToolUnavailable, "7-Zip"):
                extraction._find_archive_tool("product.7z")

    def test_rar_uses_the_controlled_unrar_discovery_fallback(self):
        with (
            mock.patch.object(extraction, "find_7z_executable", return_value=None),
            mock.patch.object(
                extraction,
                "find_unrar_executable",
                return_value=r"C:\Program Files\WinRAR\UnRAR.exe",
            ),
        ):
            self.assertEqual(
                extraction._find_archive_tool("product.rar"),
                (r"C:\Program Files\WinRAR\UnRAR.exe", "unrar"),
            )

    def test_external_extraction_enables_progress_output(self):
        archive = os.path.join(self.temp.name, "product.7z")
        with open(archive, "wb") as output:
            output.write(b"archive")
        adapter = extraction._ExternalAdapter(
            archive, "7z.exe", "7z", lambda: False
        )
        inventory = extraction.ArchiveInventory(
            archive,
            (extraction.ArchiveMember("Runtime/file.txt", 1, 1),),
            1,
            0,
        )
        archive_stat = os.stat(archive)
        adapter._signature = (archive_stat.st_size, archive_stat.st_mtime_ns)
        destination = os.path.join(self.temp.name, "external")

        with (
            mock.patch.object(extraction, "_run_external") as run,
            mock.patch.object(extraction, "_validate_extracted_tree"),
        ):
            adapter.extract(destination, inventory, lambda: False)

        arguments = run.call_args.args[0]
        self.assertIn("-bsp1", arguments)
        self.assertNotIn("-bd", arguments)


class RealExternalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dim_real_archive_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = os.path.join(self.temp.name, "source")
        self.runtime = os.path.join(self.root, "Runtime")
        os.makedirs(self.runtime)
        with open(
            os.path.join(self.runtime, "Über Café.txt"), "w", encoding="utf-8"
        ) as output:
            output.write("content")

    @unittest.skipUnless(utils.find_7z_executable(), "7-Zip is not installed")
    def test_real_7z_inventory_and_extraction(self):
        archive = os.path.join(self.temp.name, "product.7z")
        subprocess.run(
            [utils.find_7z_executable(), "a", "-t7z", "-y", archive, "Runtime"],
            cwd=self.root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **utils.hidden_subprocess_kwargs(),
        )
        destination = os.path.join(self.temp.name, "seven_zip_output")

        inventory = extraction.extract_archive_safely(archive, destination)

        self.assertIn(
            "runtime/über café.txt",
            {member.path.casefold() for member in inventory.members},
        )
        self.assertTrue(
            os.path.isfile(os.path.join(destination, "Runtime", "Über Café.txt"))
        )

    @unittest.skipUnless(_rar_creator(), "RAR creation tool is not installed")
    def test_real_rar_inventory_and_extraction(self):
        archive = os.path.join(self.temp.name, "product.rar")
        subprocess.run(
            [_rar_creator(), "a", "-idq", archive, "Runtime"],
            cwd=self.root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **utils.hidden_subprocess_kwargs(),
        )
        destination = os.path.join(self.temp.name, "rar_output")

        inventory = extraction.extract_archive_safely(archive, destination)

        self.assertIn(
            "runtime/über café.txt",
            {member.path.casefold() for member in inventory.members},
        )
        self.assertTrue(
            os.path.isfile(os.path.join(destination, "Runtime", "Über Café.txt"))
        )


class ContentExtractionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dim_worker_test_")
        self.addCleanup(self.temp.cleanup)
        self.content_dir = os.path.join(self.temp.name, "Build001", "Content")
        self.template_dir = os.path.join(self.temp.name, "Templates")
        os.makedirs(self.content_dir)
        self.space_patch = mock.patch.object(extraction, "MIN_FREE_SPACE_BYTES", 0)
        self.space_patch.start()
        self.addCleanup(self.space_patch.stop)

    def _worker(
        self,
        archive,
        policy=extraction.ConflictPolicy.CANCEL,
        *,
        prompt_on_conflicts=False,
    ):
        return extraction.ContentExtractionWorker(
            archive,
            {"Runtime", "People", "data"},
            self.content_dir,
            True,
            self.template_dir,
            conflict_policy=policy,
            prompt_on_conflicts=prompt_on_conflicts,
        )

    @staticmethod
    def _signals(worker):
        complete = []
        errors = []
        worker.extractionComplete.connect(lambda: complete.append(True))
        worker.extractionError.connect(errors.append)
        return complete, errors

    def test_preserves_nested_resource_archive_inside_daz_root(self):
        resource = _zip_bytes({"payload.txt": b"resource"})
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(
            archive,
            {
                "Wrapper/Runtime/Product/file.dsf": b"content",
                "Wrapper/Runtime/Support/resource.zip": resource,
            },
        )
        worker = self._worker(archive)
        complete, errors = self._signals(worker)

        worker.run()

        self.assertEqual(complete, [True])
        self.assertEqual(errors, [])
        self.assertEqual(worker.result.status, "success")
        resource_path = os.path.join(
            self.content_dir, "Runtime", "Support", "resource.zip"
        )
        self.assertTrue(os.path.isfile(resource_path))
        self.assertFalse(os.path.exists(os.path.join(self.content_dir, "payload.txt")))
        with zipfile.ZipFile(resource_path) as copied:
            self.assertEqual(copied.read("payload.txt"), b"resource")

    def test_rejects_daz_root_name_used_as_a_file(self):
        archive = os.path.join(self.temp.name, "root-file.zip")
        _write_zip(archive, {"Runtime": b"not a directory"})
        worker = self._worker(archive)

        worker.run()

        self.assertEqual(worker.result.status, "error")
        self.assertIn("must be a directory", worker.result.message)

    def test_extracts_one_wrapper_archive_level(self):
        inner = _zip_bytes({"Package/Runtime/file.txt": b"nested content"})
        archive = os.path.join(self.temp.name, "wrapper.zip")
        _write_zip(archive, {"Product_1.zip": inner})
        worker = self._worker(archive)
        complete, errors = self._signals(worker)

        worker.run()

        self.assertEqual(complete, [True])
        self.assertEqual(errors, [])
        with open(os.path.join(self.content_dir, "Runtime", "file.txt"), "rb") as extracted:
            self.assertEqual(extracted.read(), b"nested content")

    def test_ignores_macosx_archives_before_wrapper_classification(self):
        inner = _zip_bytes({"Runtime/file.txt": b"content"})
        archive = os.path.join(self.temp.name, "wrapper-with-metadata.zip")
        _write_zip(
            archive,
            {
                "Product.zip": inner,
                "__MACOSX/._Product.zip": b"finder metadata",
            },
        )
        worker = self._worker(archive)

        worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertTrue(
            os.path.isfile(os.path.join(self.content_dir, "Runtime", "file.txt"))
        )

    def test_rejects_a_second_wrapper_level_with_one_terminal_error(self):
        deepest = _zip_bytes({"Runtime/file.txt": b"content"})
        middle = _zip_bytes({"inner.zip": deepest})
        archive = os.path.join(self.temp.name, "wrapper.zip")
        _write_zip(archive, {"middle.zip": middle})
        worker = self._worker(archive)
        complete, errors = self._signals(worker)

        worker.run()

        self.assertEqual(complete, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(worker.result.status, "error")
        self.assertIn("nesting", errors[0].casefold())
        self.assertEqual(os.listdir(self.content_dir), [])

    def test_treats_temple_archive_as_content_not_template(self):
        inner = _zip_bytes({"Runtime/temple.duf": b"scene"})
        archive = os.path.join(self.temp.name, "wrapper.zip")
        _write_zip(archive, {"Temple Ruins.zip": inner})
        worker = self._worker(archive)

        worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertTrue(os.path.isfile(os.path.join(self.content_dir, "Runtime", "temple.duf")))
        self.assertEqual(worker.copiedTemplates, [])

    def test_copies_full_word_template_after_success(self):
        template = _zip_bytes({"readme.txt": b"template"})
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(
            archive,
            {
                "Runtime/file.txt": b"content",
                "Product Templates.zip": template,
            },
        )
        worker = self._worker(archive)

        worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertEqual(worker.copiedTemplates, ["Product Templates.zip"])
        self.assertTrue(os.path.isfile(os.path.join(self.template_dir, "Product Templates.zip")))

    def test_cancel_policy_detects_all_conflicts_before_mutating_destination(self):
        runtime = os.path.join(self.content_dir, "Runtime")
        os.makedirs(runtime)
        existing = os.path.join(runtime, "existing.txt")
        with open(existing, "wb") as output:
            output.write(b"old")
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(
            archive,
            {
                "Runtime/new.txt": b"new file",
                "Runtime/existing.txt": b"replacement",
            },
        )
        worker = self._worker(archive)
        complete, errors = self._signals(worker)

        worker.run()

        self.assertEqual(complete, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(worker.result.status, "cancelled")
        with open(existing, "rb") as current:
            self.assertEqual(current.read(), b"old")
        self.assertFalse(os.path.exists(os.path.join(runtime, "new.txt")))

    def test_replace_policy_commits_complete_result(self):
        runtime = os.path.join(self.content_dir, "Runtime")
        os.makedirs(runtime)
        existing = os.path.join(runtime, "existing.txt")
        with open(existing, "wb") as output:
            output.write(b"old")
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(
            archive,
            {
                "Runtime/existing.txt": b"replacement",
                "Runtime/new.txt": b"new file",
            },
        )
        worker = self._worker(archive, extraction.ConflictPolicy.REPLACE)

        worker.run()

        self.assertEqual(worker.result.status, "success")
        with open(existing, "rb") as current:
            self.assertEqual(current.read(), b"replacement")
        with open(os.path.join(runtime, "new.txt"), "rb") as current:
            self.assertEqual(current.read(), b"new file")
        self.assertFalse(any("dim-backup" in name for name in os.listdir(runtime)))

    def test_skip_policy_preserves_conflict_and_adds_non_conflicting_file(self):
        runtime = os.path.join(self.content_dir, "Runtime")
        os.makedirs(runtime)
        existing = os.path.join(runtime, "existing.txt")
        with open(existing, "wb") as output:
            output.write(b"old")
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(
            archive,
            {
                "Runtime/existing.txt": b"replacement",
                "Runtime/new.txt": b"new file",
            },
        )
        worker = self._worker(archive, extraction.ConflictPolicy.SKIP)

        worker.run()

        self.assertEqual(worker.result.status, "success")
        with open(existing, "rb") as current:
            self.assertEqual(current.read(), b"old")
        self.assertTrue(os.path.isfile(os.path.join(runtime, "new.txt")))
        self.assertEqual(len(worker.result.skipped_files), 1)

    def test_interactive_import_does_not_prompt_without_exact_conflicts(self):
        runtime = os.path.join(self.content_dir, "Runtime")
        os.makedirs(runtime)
        with open(os.path.join(runtime, "unrelated.txt"), "wb") as output:
            output.write(b"existing")
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(archive, {"Runtime/new.txt": b"new"})
        worker = self._worker(archive, prompt_on_conflicts=True)
        prompts = []
        worker.conflictsDetected.connect(prompts.append)

        worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertEqual(prompts, [])
        self.assertEqual(worker.result.modified_builds, ["Build001"])

    def test_interactive_import_prompts_once_for_content_and_template_conflicts(self):
        runtime = os.path.join(self.content_dir, "Runtime")
        os.makedirs(runtime)
        existing_content = os.path.join(runtime, "existing.txt")
        with open(existing_content, "wb") as output:
            output.write(b"old")
        os.makedirs(self.template_dir)
        existing_template = os.path.join(self.template_dir, "Product Templates.zip")
        with open(existing_template, "wb") as output:
            output.write(b"old template")
        template = _zip_bytes({"readme.txt": b"template"})
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(
            archive,
            {
                "Runtime/existing.txt": b"replacement",
                "Product Templates.zip": template,
            },
        )
        worker = self._worker(archive, prompt_on_conflicts=True)
        prompts = []

        def skip(conflicts):
            prompts.append(tuple(conflicts))
            worker.resolveConflictPolicy(extraction.ConflictPolicy.SKIP)

        worker.conflictsDetected.connect(skip)
        worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertEqual(len(prompts), 1)
        self.assertEqual(
            {os.path.abspath(path).casefold() for path in prompts[0]},
            {
                os.path.abspath(existing_content).casefold(),
                os.path.abspath(existing_template).casefold(),
            },
        )
        self.assertEqual(worker.result.modified_builds, [])
        self.assertEqual(worker.result.copied_templates, [])
        self.assertEqual(len(worker.result.skipped_files), 2)

    def test_cancelling_interactive_conflict_wait_rolls_back_without_changes(self):
        runtime = os.path.join(self.content_dir, "Runtime")
        os.makedirs(runtime)
        existing = os.path.join(runtime, "existing.txt")
        with open(existing, "wb") as output:
            output.write(b"old")
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(archive, {"Runtime/existing.txt": b"replacement"})
        worker = self._worker(archive, prompt_on_conflicts=True)
        prompts = []

        def cancel(conflicts):
            prompts.append(tuple(conflicts))
            worker.requestCancellation()

        worker.conflictsDetected.connect(cancel)
        worker.run()

        self.assertEqual(worker.result.status, "cancelled")
        self.assertEqual(len(prompts), 1)
        with open(existing, "rb") as current:
            self.assertEqual(current.read(), b"old")

    def test_cooperative_cancellation_emits_only_cancelled_terminal_status(self):
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(archive, {"Runtime/file.txt": b"content"})
        worker = self._worker(archive)
        worker._cancelled = lambda: True
        complete, errors = self._signals(worker)
        results = []
        worker.resultReady.connect(results.append)

        worker.run()

        self.assertEqual(complete, [])
        self.assertEqual(errors, ["Extraction cancelled."])
        self.assertEqual(results, [worker.result])
        self.assertTrue(worker.result.cancelled)
        self.assertEqual(os.listdir(self.content_dir), [])


class ArchivePlanningWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dim_planning_test_")
        self.addCleanup(self.temp.cleanup)
        self.space_patch = mock.patch.object(extraction, "MIN_FREE_SPACE_BYTES", 0)
        self.space_patch.start()
        self.addCleanup(self.space_patch.stop)

    def test_direct_plan_is_committed_without_extracting_outer_again(self):
        template = _zip_bytes({"readme.txt": b"template"})
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(
            archive,
            {
                "Wrapper/Runtime/file.txt": b"content",
                "Wrapper/Product Template.zip": template,
            },
        )
        real_extract = extraction.extract_archive_safely
        calls = []

        def counting_extract(path, *args, **kwargs):
            calls.append(os.path.abspath(path))
            return real_extract(path, *args, **kwargs)

        planner = extraction.ArchivePlanningWorker(archive, {"Runtime"}, True)
        planning_results = []
        plans = []
        errors = []
        planner.resultReady.connect(planning_results.append)
        planner.planReady.connect(plans.append)
        planner.planningError.connect(errors.append)
        with mock.patch.object(extraction, "extract_archive_safely", side_effect=counting_extract):
            planner.run()

        self.assertEqual(errors, [])
        self.assertEqual(len(planning_results), 1)
        self.assertEqual(plans, [planning_results[0].plan])
        plan = plans[0]
        self.assertTrue(plan.is_direct_content)
        self.assertEqual(len(plan.content_archives), 0)
        self.assertEqual(
            [item.relative_path for item in plan.template_archives],
            ["Wrapper/Product Template.zip"],
        )
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], os.path.abspath(archive))
        self.assertTrue(
            os.path.commonpath((plan.stage_root, calls[0])) == plan.stage_root
        )

        content_dir = os.path.join(self.temp.name, "Build001", "Content")
        template_dir = os.path.join(self.temp.name, "Templates")
        os.makedirs(content_dir)
        worker = extraction.ContentExtractionWorker(
            plan,
            {"Runtime"},
            content_dir,
            True,
            template_dir,
        )
        with mock.patch.object(
            extraction,
            "extract_archive_safely",
            side_effect=AssertionError("outer archive was extracted twice"),
        ):
            worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertTrue(os.path.isfile(os.path.join(content_dir, "Runtime", "file.txt")))
        self.assertEqual(worker.copiedTemplates, ["Product Template.zip"])
        self.assertFalse(os.path.exists(plan.stage_root))

    def test_wrapper_plan_returns_unique_relative_paths_without_extracting_parts(self):
        first = _zip_bytes({"Runtime/one.txt": b"one"})
        second = _zip_bytes({"People/two.txt": b"two"})
        template = _zip_bytes({"guide.txt": b"template"})
        archive = os.path.join(self.temp.name, "wrapper.zip")
        _write_zip(
            archive,
            {
                "Vendor A/Product_1of2.zip": first,
                "Vendor B/Product_2of2.zip": second,
                "Vendor B/Product Templates.zip": template,
            },
        )
        real_extract = extraction.extract_archive_safely
        calls = []

        def counting_extract(path, *args, **kwargs):
            calls.append(os.path.abspath(path))
            return real_extract(path, *args, **kwargs)

        with mock.patch.object(extraction, "extract_archive_safely", side_effect=counting_extract):
            plan = extraction.plan_archive_import(
                archive, {"Runtime", "People"}, True
            )

        self.assertFalse(plan.is_direct_content)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], os.path.abspath(archive))
        self.assertTrue(
            os.path.commonpath((plan.stage_root, calls[0])) == plan.stage_root
        )
        self.assertEqual(
            [item.relative_path for item in plan.content_archives],
            ["Vendor A/Product_1of2.zip", "Vendor B/Product_2of2.zip"],
        )
        self.assertEqual(
            [item.relative_path for item in plan.template_archives],
            ["Vendor B/Product Templates.zip"],
        )
        self.assertTrue(all(os.path.isfile(item.staged_path) for item in plan.content_archives))
        plan.cleanup()
        self.assertFalse(os.path.exists(plan.stage_root))

    def test_same_basename_wrapper_members_remain_distinct(self):
        content = _zip_bytes({"Runtime/file.txt": b"content"})
        archive = os.path.join(self.temp.name, "wrapper.zip")
        _write_zip(
            archive,
            {"Vendor A/Content.zip": content, "Vendor B/Content.zip": content},
        )

        plan = extraction.plan_archive_import(archive, {"Runtime"}, True)

        self.assertEqual(
            {item.relative_path for item in plan.content_archives},
            {"Vendor A/Content.zip", "Vendor B/Content.zip"},
        )
        self.assertEqual(len({item.staged_path for item in plan.content_archives}), 2)
        plan.cleanup()

    def test_planning_cancellation_emits_one_typed_terminal_result(self):
        archive = os.path.join(self.temp.name, "product.zip")
        _write_zip(archive, {"Runtime/file.txt": b"content"})
        worker = extraction.ArchivePlanningWorker(archive, {"Runtime"}, True)
        worker.isInterruptionRequested = lambda: True
        results = []
        plans = []
        errors = []
        worker.resultReady.connect(results.append)
        worker.planReady.connect(plans.append)
        worker.planningError.connect(errors.append)

        worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "cancelled")
        self.assertEqual(plans, [])
        self.assertEqual(errors, ["Extraction cancelled."])


class MultiBuildExtractionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dim_multi_worker_test_")
        self.addCleanup(self.temp.cleanup)
        self.builds_dir = os.path.join(self.temp.name, "Builds")
        self.templates_dir = os.path.join(self.temp.name, "Templates")
        os.makedirs(self.builds_dir)
        self.builds_patch = mock.patch.object(utils, "BUILDS_DIR", self.builds_dir)
        self.builds_patch.start()
        self.addCleanup(self.builds_patch.stop)
        self.space_patch = mock.patch.object(extraction, "MIN_FREE_SPACE_BYTES", 0)
        self.space_patch.start()
        self.addCleanup(self.space_patch.stop)

    def test_success_returns_gui_apply_payload_without_mutating_session(self):
        build = Build(
            id="build_001",
            folder="Build001",
            part=1,
            guid=str(uuid.uuid4()),
            content_status="empty",
        )
        session = Session(builds=[build], next_build_number=2)
        first = os.path.join(self.temp.name, "Product_1of2.zip")
        second = os.path.join(self.temp.name, "Product_2of2.zip")
        _write_zip(first, {"Runtime/one.txt": b"one"})
        _write_zip(second, {"People/two.txt": b"two"})
        worker = extraction.MultiBuildExtractionWorker(
            [second, first],
            [],
            {"Runtime", "People"},
            session,
            True,
            self.templates_dir,
        )
        results = []
        complete = []
        errors = []
        worker.resultReady.connect(results.append)
        worker.extractionComplete.connect(complete.append)
        worker.extractionError.connect(errors.append)

        worker.run()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertIs(results[0], worker.result)
        self.assertEqual(complete, [["Build001", "Build002"]])
        self.assertEqual(worker.result.status, "success")
        self.assertEqual(len(session.builds), 1)
        self.assertIs(session.builds[0], build)
        self.assertEqual(session.next_build_number, 2)
        self.assertEqual(build.content_status, "empty")
        self.assertEqual(len(worker.result.build_updates), 2)
        existing_update, new_update = worker.result.build_updates
        self.assertEqual(existing_update.build_id, "build_001")
        self.assertIsNone(existing_update.new_build)
        self.assertEqual(new_update.build_id, "build_002")
        self.assertEqual(new_update.new_build["folder"], "Build002")
        self.assertEqual(worker.result.next_build_number, 3)
        self.assertTrue(
            os.path.isfile(os.path.join(self.builds_dir, "Build001", "Content", "Runtime", "one.txt"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.builds_dir, "Build002", "Content", "People", "two.txt"))
        )

    def test_interactive_multi_build_import_skips_prompt_for_empty_targets(self):
        build = Build(
            id="build_001",
            folder="Build001",
            part=1,
            guid=str(uuid.uuid4()),
            content_status="empty",
        )
        session = Session(builds=[build], next_build_number=2)
        archive = os.path.join(self.temp.name, "Product.zip")
        _write_zip(archive, {"Runtime/new.txt": b"new"})
        worker = extraction.MultiBuildExtractionWorker(
            [archive],
            [],
            {"Runtime"},
            session,
            True,
            self.templates_dir,
            prompt_on_conflicts=True,
        )
        prompts = []
        worker.conflictsDetected.connect(prompts.append)

        worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertEqual(prompts, [])
        self.assertEqual(worker.result.modified_builds, ["Build001"])

    def test_error_emits_one_typed_result_and_leaves_session_unchanged(self):
        build = Build(
            id="build_001",
            folder="Build001",
            part=1,
            guid=str(uuid.uuid4()),
            content_status="empty",
        )
        session = Session(builds=[build], next_build_number=2)
        archive = os.path.join(self.temp.name, "unsafe.zip")
        _write_zip(archive, {"../escape.txt": b"escape"})
        worker = extraction.MultiBuildExtractionWorker(
            [archive], [], {"Runtime"}, session, True, self.templates_dir
        )
        results = []
        errors = []
        worker.resultReady.connect(results.append)
        worker.extractionError.connect(errors.append)

        worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(worker.result.status, "error")
        self.assertEqual(session.builds, [build])
        self.assertEqual(session.next_build_number, 2)
        self.assertEqual(build.content_status, "empty")

    def test_consumes_wrapper_plan_without_reextracting_outer(self):
        build = Build(
            id="build_001",
            folder="Build001",
            part=1,
            guid=str(uuid.uuid4()),
            content_status="empty",
        )
        session = Session(builds=[build], next_build_number=2)
        first = _zip_bytes({"Runtime/one.txt": b"one"})
        second = _zip_bytes({"People/two.txt": b"two"})
        archive = os.path.join(self.temp.name, "wrapper.zip")
        _write_zip(
            archive,
            {"Product_1of2.zip": first, "Product_2of2.zip": second},
        )
        real_extract = extraction.extract_archive_safely
        calls = []

        def counting_extract(path, *args, **kwargs):
            calls.append(os.path.abspath(path))
            return real_extract(path, *args, **kwargs)

        with mock.patch.object(extraction, "extract_archive_safely", side_effect=counting_extract):
            plan = extraction.plan_archive_import(
                archive, {"Runtime", "People"}, True
            )
            worker = extraction.MultiBuildExtractionWorker(
                plan.content_archives,
                plan.template_archives,
                {"Runtime", "People"},
                session,
                True,
                self.templates_dir,
                import_plan=plan,
            )
            worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertNotIn(os.path.abspath(archive), calls)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(session.builds), 1)
        self.assertFalse(os.path.exists(plan.stage_root))
        self.assertTrue(
            os.path.isfile(os.path.join(self.builds_dir, "Build001", "Content", "Runtime", "one.txt"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.builds_dir, "Build002", "Content", "People", "two.txt"))
        )

    def test_wrapper_plan_preserves_user_selected_order(self):
        build = Build(
            id="build_001",
            folder="Build001",
            part=1,
            guid=str(uuid.uuid4()),
            content_status="empty",
        )
        session = Session(builds=[build], next_build_number=2)
        archive = os.path.join(self.temp.name, "wrapper-order.zip")
        _write_zip(
            archive,
            {
                "Product_1of2.zip": _zip_bytes({"Runtime/one.txt": b"one"}),
                "Product_2of2.zip": _zip_bytes({"People/two.txt": b"two"}),
            },
        )
        plan = extraction.plan_archive_import(
            archive, {"Runtime", "People"}, True
        )
        selected_order = tuple(reversed(plan.content_archives))
        worker = extraction.MultiBuildExtractionWorker(
            selected_order,
            plan.template_archives,
            {"Runtime", "People"},
            session,
            True,
            self.templates_dir,
            import_plan=plan,
        )

        worker.run()

        self.assertEqual(worker.result.status, "success")
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.builds_dir, "Build001", "Content", "People", "two.txt"
                )
            )
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.builds_dir, "Build002", "Content", "Runtime", "one.txt"
                )
            )
        )

    def test_wrapper_plan_revalidates_final_content_selection(self):
        build = Build(
            id="build_001",
            folder="Build001",
            part=1,
            guid=str(uuid.uuid4()),
            content_status="empty",
        )
        session = Session(builds=[build], next_build_number=2)
        archive = os.path.join(self.temp.name, "wrapper-selection.zip")
        _write_zip(
            archive,
            {
                "Product_1of2.zip": _zip_bytes({"Runtime/one.txt": b"one"}),
                "Product_2of2.zip": _zip_bytes({"People/two.txt": b"two"}),
            },
        )
        plan = extraction.plan_archive_import(
            archive, {"Runtime", "People"}, True
        )
        worker = extraction.MultiBuildExtractionWorker(
            [plan.content_archives[1]],
            [],
            {"Runtime", "People"},
            session,
            True,
            self.templates_dir,
            import_plan=plan,
        )

        worker.run()

        self.assertEqual(worker.result.status, "error")
        self.assertIn("missing parts", worker.result.message)
        self.assertFalse(
            os.path.exists(
                os.path.join(self.builds_dir, "Build001", "Content", "People")
            )
        )

    def test_rejects_selection_outside_wrapper_plan(self):
        build = Build(
            id="build_001",
            folder="Build001",
            part=1,
            guid=str(uuid.uuid4()),
            content_status="empty",
        )
        session = Session(builds=[build], next_build_number=2)
        inner = _zip_bytes({"Runtime/file.txt": b"content"})
        archive = os.path.join(self.temp.name, "wrapper.zip")
        _write_zip(archive, {"Product.zip": inner})
        outside = os.path.join(self.temp.name, "outside.zip")
        _write_zip(outside, {"Runtime/outside.txt": b"outside"})
        plan = extraction.plan_archive_import(archive, {"Runtime"}, True)
        worker = extraction.MultiBuildExtractionWorker(
            [outside],
            [],
            {"Runtime"},
            session,
            True,
            self.templates_dir,
            import_plan=plan,
        )

        worker.run()

        self.assertEqual(worker.result.status, "error")
        self.assertIn("not part of the import plan", worker.result.message)
        self.assertFalse(os.path.exists(plan.stage_root))
        self.assertEqual(session.builds, [build])


class TransactionRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dim_transaction_test_")
        self.addCleanup(self.temp.cleanup)
        self.space_patch = mock.patch.object(extraction, "MIN_FREE_SPACE_BYTES", 0)
        self.space_patch.start()
        self.addCleanup(self.space_patch.stop)

    def test_copy_failure_restores_every_replaced_file(self):
        source_root = os.path.join(self.temp.name, "source")
        target_root = os.path.join(self.temp.name, "target")
        os.makedirs(source_root)
        os.makedirs(target_root)
        for name, value in (("one.txt", b"new one"), ("two.txt", b"new two")):
            with open(os.path.join(source_root, name), "wb") as output:
                output.write(value)
        for name, value in (("one.txt", b"old one"), ("two.txt", b"old two")):
            with open(os.path.join(target_root, name), "wb") as output:
                output.write(value)

        transaction = extraction._FileTransaction(extraction.ConflictPolicy.REPLACE)
        transaction.add_file(os.path.join(source_root, "one.txt"), target_root)
        transaction.add_file(os.path.join(source_root, "two.txt"), target_root)
        real_copy = extraction._copy_file
        calls = 0

        def fail_second_copy(source, destination, cancel_check):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated write failure")
            return real_copy(source, destination, cancel_check)

        with mock.patch.object(extraction, "_copy_file", side_effect=fail_second_copy):
            with self.assertRaisesRegex(OSError, "simulated write failure"):
                transaction.commit(lambda: False)

        with open(os.path.join(target_root, "one.txt"), "rb") as current:
            self.assertEqual(current.read(), b"old one")
        with open(os.path.join(target_root, "two.txt"), "rb") as current:
            self.assertEqual(current.read(), b"old two")
        self.assertFalse(any("dim-backup" in name for name in os.listdir(target_root)))
        self.assertFalse(any("dim-new" in name for name in os.listdir(target_root)))

    def test_conflict_appearing_after_preflight_honours_cancel_and_skip(self):
        source = os.path.join(self.temp.name, "incoming.txt")
        with open(source, "wb") as output:
            output.write(b"incoming")

        for policy in (
            extraction.ConflictPolicy.CANCEL,
            extraction.ConflictPolicy.SKIP,
        ):
            with self.subTest(policy=policy):
                target_root = os.path.join(self.temp.name, policy.value)
                os.makedirs(target_root)
                target = os.path.join(target_root, "incoming.txt")
                transaction = extraction._FileTransaction(policy)
                transaction.add_file(source, target_root)
                original_planned = transaction._planned

                def race_after_preflight(cancel_check=None):
                    planned = original_planned(cancel_check)
                    with open(target, "wb") as output:
                        output.write(b"racer")
                    return planned

                with mock.patch.object(
                    transaction, "_planned", side_effect=race_after_preflight
                ):
                    if policy is extraction.ConflictPolicy.CANCEL:
                        with self.assertRaises(extraction.ExtractionConflict):
                            transaction.commit(lambda: False)
                    else:
                        transaction.commit(lambda: False)

                with open(target, "rb") as current:
                    self.assertEqual(current.read(), b"racer")

    def test_conflict_appearing_during_copy_honours_cancel_and_skip(self):
        source = os.path.join(self.temp.name, "during-copy.txt")
        with open(source, "wb") as output:
            output.write(b"incoming")
        real_copy = extraction._copy_file

        for policy in (
            extraction.ConflictPolicy.CANCEL,
            extraction.ConflictPolicy.SKIP,
        ):
            with self.subTest(policy=policy):
                target_root = os.path.join(
                    self.temp.name, f"during-{policy.value}"
                )
                os.makedirs(target_root)
                target = os.path.join(target_root, "during-copy.txt")
                transaction = extraction._FileTransaction(policy)
                transaction.add_file(source, target_root)

                def copy_then_race(source_path, destination, cancel_check):
                    real_copy(source_path, destination, cancel_check)
                    with open(target, "wb") as output:
                        output.write(b"racer")

                with mock.patch.object(
                    extraction, "_copy_file", side_effect=copy_then_race
                ):
                    if policy is extraction.ConflictPolicy.CANCEL:
                        with self.assertRaises(extraction.ExtractionConflict):
                            transaction.commit(lambda: False)
                    else:
                        transaction.commit(lambda: False)

                with open(target, "rb") as current:
                    self.assertEqual(current.read(), b"racer")

    def test_interactive_commit_prompts_once_for_a_late_conflict(self):
        source = os.path.join(self.temp.name, "late.txt")
        target_root = os.path.join(self.temp.name, "late-target")
        target = os.path.join(target_root, "late.txt")
        os.makedirs(target_root)
        with open(source, "wb") as output:
            output.write(b"incoming")
        transaction = extraction._FileTransaction(extraction.ConflictPolicy.CANCEL)
        transaction.add_file(source, target_root)
        gate = extraction._ConflictDecisionGate()
        prompts = []

        def race_publish(_temporary, target_path):
            with open(target_path, "wb") as output:
                output.write(b"racer")
            raise FileExistsError(target_path)

        def choose_skip(conflicts):
            prompts.append(tuple(conflicts))
            gate.resolve(extraction.ConflictPolicy.SKIP)

        with mock.patch.object(
            transaction,
            "_publish_without_replace",
            side_effect=race_publish,
        ):
            extraction._commit_with_optional_prompt(
                transaction,
                prompt_on_conflicts=True,
                gate=gate,
                emit_conflicts=choose_skip,
                cancel_check=lambda: False,
            )

        self.assertEqual(prompts, [(target,)])
        self.assertEqual(transaction.applied, [])
        self.assertEqual(transaction.skipped, [target])
        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"racer")

    def test_replace_keeps_existing_file_visible_until_copy_finishes(self):
        source = os.path.join(self.temp.name, "replacement.txt")
        target_root = os.path.join(self.temp.name, "visible-target")
        target = os.path.join(target_root, "replacement.txt")
        os.makedirs(target_root)
        with open(source, "wb") as output:
            output.write(b"new")
        with open(target, "wb") as output:
            output.write(b"old")
        transaction = extraction._FileTransaction(
            extraction.ConflictPolicy.REPLACE
        )
        transaction.add_file(source, target_root)
        real_copy = extraction._copy_file

        def assert_old_then_copy(source_path, destination, cancel_check):
            with open(target, "rb") as current:
                self.assertEqual(current.read(), b"old")
            real_copy(source_path, destination, cancel_check)

        with mock.patch.object(
            extraction, "_copy_file", side_effect=assert_old_then_copy
        ):
            transaction.commit(lambda: False)

        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"new")
        transaction.rollback()
        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"old")

    def test_rejects_reparse_component_between_boundary_and_content_root(self):
        source = os.path.join(self.temp.name, "incoming.txt")
        builds_root = os.path.join(self.temp.name, "Builds")
        build_root = os.path.join(builds_root, "Build001")
        content_root = os.path.join(build_root, "Content")
        os.makedirs(content_root)
        with open(source, "wb") as output:
            output.write(b"incoming")

        transaction = extraction._FileTransaction(
            extraction.ConflictPolicy.REPLACE
        )
        transaction.add_file(
            source,
            content_root,
            containment_root=builds_root,
        )
        real_check = extraction._is_link_or_reparse

        def mark_build_as_reparse(path):
            if os.path.abspath(path) == os.path.abspath(build_root):
                return True
            return real_check(path)

        with mock.patch.object(
            extraction,
            "_is_link_or_reparse",
            side_effect=mark_build_as_reparse,
        ):
            with self.assertRaisesRegex(
                extraction.UnsafeArchiveError,
                "link or reparse point",
            ):
                transaction.commit(lambda: False)

        self.assertFalse(os.path.exists(os.path.join(content_root, "incoming.txt")))

    def test_failed_rollback_remains_visible_and_retryable(self):
        source = os.path.join(self.temp.name, "replacement.txt")
        target_root = os.path.join(self.temp.name, "retry-target")
        target = os.path.join(target_root, "replacement.txt")
        os.makedirs(target_root)
        with open(source, "wb") as output:
            output.write(b"new")
        with open(target, "wb") as output:
            output.write(b"old")

        transaction = extraction._FileTransaction(
            extraction.ConflictPolicy.REPLACE
        )
        transaction.add_file(source, target_root)
        transaction.commit(lambda: False)
        real_replace = extraction.os.replace

        def fail_backup_restore(source_path, target_path):
            if os.path.basename(source_path).endswith(".backup"):
                raise PermissionError("simulated rollback failure")
            return real_replace(source_path, target_path)

        with mock.patch.object(
            extraction.os,
            "replace",
            side_effect=fail_backup_restore,
        ):
            with self.assertRaisesRegex(
                extraction.ExtractionRollbackError,
                "Rollback incomplete",
            ):
                transaction.rollback()

        self.assertTrue(transaction.rollback_pending)
        transaction.rollback()
        self.assertFalse(transaction.rollback_pending)
        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"old")

    def test_failed_publish_keeps_partial_rollback_retryable(self):
        source = os.path.join(self.temp.name, "replacement-after-failure.txt")
        target_root = os.path.join(self.temp.name, "partial-target")
        target = os.path.join(target_root, "replacement-after-failure.txt")
        os.makedirs(target_root)
        with open(source, "wb") as output:
            output.write(b"new")
        with open(target, "wb") as output:
            output.write(b"old")

        transaction = extraction._FileTransaction(
            extraction.ConflictPolicy.REPLACE
        )
        transaction.add_file(source, target_root)
        real_replace = extraction.os.replace

        def fail_publish_and_restore(source_path, target_path):
            source_name = os.path.basename(source_path)
            if source_name.endswith(".new"):
                raise OSError("simulated publish failure")
            if source_name.endswith(".backup"):
                raise PermissionError("simulated rollback failure")
            return real_replace(source_path, target_path)

        with mock.patch.object(
            extraction.os,
            "replace",
            side_effect=fail_publish_and_restore,
        ):
            with self.assertRaises(extraction.ExtractionRollbackError) as raised:
                transaction.commit(lambda: False)

        self.assertIn("simulated publish failure", str(raised.exception.original_error))
        self.assertTrue(transaction.rollback_pending)

        transaction.rollback()

        self.assertFalse(transaction.rollback_pending)
        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"old")

    def test_worker_publishes_one_error_when_rollback_needs_retry(self):
        content_dir = os.path.join(self.temp.name, "Build001", "Content")
        template_dir = os.path.join(self.temp.name, "Templates")
        target = os.path.join(content_dir, "Runtime", "file.txt")
        os.makedirs(os.path.dirname(target))
        with open(target, "wb") as output:
            output.write(b"old")
        archive = os.path.join(self.temp.name, "rollback-failure.zip")
        _write_zip(archive, {"Runtime/file.txt": b"new"})
        worker = extraction.ContentExtractionWorker(
            archive,
            {"Runtime"},
            content_dir,
            True,
            template_dir,
            conflict_policy=extraction.ConflictPolicy.REPLACE,
        )
        results = []
        errors = []
        completed = []
        worker.resultReady.connect(results.append)
        worker.extractionError.connect(errors.append)
        worker.extractionComplete.connect(lambda: completed.append(True))
        real_commit = extraction._FileTransaction.commit
        real_replace = extraction.os.replace

        def commit_then_fail(transaction, cancel_check):
            real_commit(transaction, cancel_check)
            raise OSError("simulated post-commit failure")

        def fail_backup_restore(source_path, target_path):
            if os.path.basename(source_path).endswith(".backup"):
                raise PermissionError("simulated rollback failure")
            return real_replace(source_path, target_path)

        with (
            mock.patch.object(
                extraction._FileTransaction,
                "commit",
                new=commit_then_fail,
            ),
            mock.patch.object(
                extraction.os,
                "replace",
                side_effect=fail_backup_restore,
            ),
        ):
            worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(completed, [])
        self.assertEqual(worker.result.status, "error")
        self.assertIn("Rollback incomplete", worker.result.message)
        self.assertTrue(worker.result.rollback_pending)

        worker.result.rollback()

        self.assertFalse(worker.result.rollback_pending)
        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"old")

    def test_deferred_finalize_can_roll_back_after_session_save_failure(self):
        content_dir = os.path.join(self.temp.name, "Build001", "Content")
        template_dir = os.path.join(self.temp.name, "Templates")
        target = os.path.join(content_dir, "Runtime", "file.txt")
        os.makedirs(os.path.dirname(target))
        with open(target, "wb") as output:
            output.write(b"old")
        archive = os.path.join(self.temp.name, "replacement.zip")
        _write_zip(archive, {"Runtime/file.txt": b"new"})
        worker = extraction.ContentExtractionWorker(
            archive,
            {"Runtime"},
            content_dir,
            True,
            template_dir,
            conflict_policy=extraction.ConflictPolicy.REPLACE,
            defer_finalize=True,
        )

        worker.run()
        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"new")

        worker.result.rollback()

        with open(target, "rb") as current:
            self.assertEqual(current.read(), b"old")
        self.assertFalse(any("dim-backup" in name for name in os.listdir(os.path.dirname(target))))

    def test_failed_backup_cleanup_stays_outside_packageable_content(self):
        builds_root = os.path.join(self.temp.name, "Builds")
        content_root = os.path.join(builds_root, "Build001", "Content")
        source = os.path.join(self.temp.name, "replacement-cleanup.txt")
        target = os.path.join(content_root, "Runtime", "replacement-cleanup.txt")
        os.makedirs(os.path.dirname(target))
        with open(source, "wb") as output:
            output.write(b"new")
        with open(target, "wb") as output:
            output.write(b"old")

        transaction = extraction._FileTransaction(
            extraction.ConflictPolicy.REPLACE
        )
        transaction.add_file(
            source,
            content_root,
            target_name="Runtime/replacement-cleanup.txt",
            containment_root=builds_root,
        )
        transaction.commit(lambda: False)
        backup = transaction._committed[0].backup
        real_remove = extraction.os.remove

        def fail_backup_cleanup(path):
            if os.path.abspath(path) == os.path.abspath(backup):
                raise PermissionError("simulated cleanup failure")
            return real_remove(path)

        with mock.patch.object(
            extraction.os, "remove", side_effect=fail_backup_cleanup
        ):
            transaction.finalize()

        self.assertTrue(os.path.isfile(backup))
        self.assertNotEqual(
            os.path.commonpath((os.path.abspath(backup), os.path.abspath(content_root))),
            os.path.abspath(content_root),
        )

    def test_staging_checks_space_for_the_additional_copy(self):
        source = os.path.join(self.temp.name, "source.bin")
        with open(source, "wb") as output:
            output.write(b"12345")
        destination = os.path.join(self.temp.name, "stage")
        analysis = extraction._TreeAnalysis(
            content_files=[(source, "Runtime/source.bin")]
        )

        with mock.patch.object(extraction, "_ensure_free_space") as check:
            extraction._stage_content(analysis, destination, lambda: False)

        check.assert_called_once_with(destination, 5)

    def test_commit_aggregates_space_for_roots_on_the_same_volume(self):
        first_source = os.path.join(self.temp.name, "first.bin")
        second_source = os.path.join(self.temp.name, "second.bin")
        with open(first_source, "wb") as output:
            output.write(b"123")
        with open(second_source, "wb") as output:
            output.write(b"4567")
        first_root = os.path.join(self.temp.name, "first-root")
        second_root = os.path.join(self.temp.name, "second-root")
        transaction = extraction._FileTransaction(
            extraction.ConflictPolicy.REPLACE
        )
        transaction.add_file(first_source, first_root)
        transaction.add_file(second_source, second_root)

        with mock.patch.object(extraction, "_ensure_free_space") as check:
            transaction.commit(lambda: False)

        self.assertEqual(check.call_count, 1)
        self.assertEqual(check.call_args.args[1], 7)
        transaction.finalize()


if __name__ == "__main__":
    unittest.main()
