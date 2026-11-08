import os
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from xml.etree import ElementTree

from PIL import Image

import packaging_utils
from naming_utils import build_dim_zip_filename, build_support_cover_filename
from packaging_utils import (
    BatchPackagingWorker,
    PackageInventory,
    PackageResult,
    PackageSpec,
    PackageStatus,
    PackagingError,
    PackagingPipeline,
    find_7z_executable,
    validate_package_spec,
)


class PackagingTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.builds_dir = self.root / "Builds"
        self.build_dir = self.builds_dir / "Build1"
        self.content_dir = self.build_dir / "Content"
        self.destination = self.root / "Output"
        self.content_dir.mkdir(parents=True)
        self.destination.mkdir()
        self._write("People/Example/Product.duf", b"product")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, relative: str, value: bytes) -> Path:
        path = self.content_dir.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def _spec(self, **changes) -> PackageSpec:
        values = {
            "content_dir": str(self.content_dir),
            "store": "Renderotica",
            "product_name": "Temple Ruins",
            "prefix": "RE",
            "sku": "70127",
            "product_part": 1,
            "product_tags": "DAZStudio4_5",
            "image_path": None,
            "clean_support": False,
            "guid": str(uuid.uuid4()),
            "destination_folder": str(self.destination),
        }
        values.update(changes)
        return PackageSpec(**values)

    def _pipeline(self, **changes) -> PackagingPipeline:
        pipeline = PackagingPipeline(self._spec(**changes))
        pipeline.seven_zip_path = None
        return pipeline

    def _output_path(self, spec: PackageSpec) -> Path:
        return Path(spec.destination_folder) / build_dim_zip_filename(
            spec.prefix,
            spec.sku,
            spec.product_part,
            spec.product_name,
        )

    @staticmethod
    def _snapshot(directory: Path) -> dict[str, bytes]:
        return {
            path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file() and not path.is_symlink()
        }


class PackageInventoryTests(PackagingTestCase):
    def test_inventory_ignores_system_files_links_and_cleaned_support(self):
        self._write("Thumbs.db", b"junk")
        self._write("__MACOSX/metadata", b"junk")
        self._write(".dimcreator-fileop-deadbeef/stale.backup", b"old")
        self._write(
            ".Product.duf.dim-backup-0123456789abcdef0123456789abcdef",
            b"old",
        )
        self._write("Runtime/Support/old.dsx", b"old")
        link = self.content_dir / "People" / "linked.duf"
        try:
            link.symlink_to(self.content_dir / "People" / "Example" / "Product.duf")
            link_created = True
        except (OSError, NotImplementedError):
            link_created = False

        inventory = PackageInventory.from_content(
            self.content_dir,
            clean_support=True,
        )

        self.assertEqual(
            inventory.archive_members,
            ("Content/People/Example/Product.duf",),
        )
        if link_created:
            self.assertNotIn("Content/People/linked.duf", inventory.archive_members)

    def test_inventory_preserves_unicode_names(self):
        self._write("People/Über Café.duf", b"unicode")
        inventory = PackageInventory.from_content(self.content_dir)
        self.assertIn("Content/People/Über Café.duf", inventory.archive_members)

    def test_inventory_rejects_case_insensitive_collisions(self):
        real_file = self.content_dir / "real.duf"
        real_file.write_bytes(b"data")

        class FakeEntry:
            def __init__(self, name):
                self.name = name
                self.path = str(real_file)

            def stat(self, *, follow_symlinks):
                return real_file.stat()

            def is_symlink(self):
                return False

        with mock.patch(
            "packaging_utils.os.scandir",
            return_value=[FakeEntry("Foo.duf"), FakeEntry("foo.DUF")],
        ):
            with self.assertRaisesRegex(PackagingError, "name collision"):
                PackageInventory.from_content(self.content_dir)

    def test_inventory_rejects_windows_device_names(self):
        self._write("People/CON.duf", b"unsafe")
        with self.assertRaisesRegex(PackagingError, "reserved Windows name"):
            PackageInventory.from_content(self.content_dir)


class PackagingPipelineTests(PackagingTestCase):
    def test_7zip_output_refreshes_watchdog_without_duplicate_progress(self):
        events = []

        class Stdout:
            def close(self):
                events.append("close")

        class Process:
            def __init__(self):
                self.stdout = Stdout()
                self._polls = iter((None, None, None, 0, 0))

            def poll(self):
                return next(self._polls, 0)

            def wait(self):
                return 0

        class OutputQueue:
            def __init__(self):
                self.items = iter((b"10%\n", b"10%\n", b"10%\n", None))

            def get(self, timeout):
                return next(self.items)

        class ReaderThread:
            def __init__(self, target, daemon):
                pass

            def start(self):
                pass

            def join(self, timeout):
                events.append("join")

        pipeline = self._pipeline()
        pipeline.seven_zip_path = "7z"
        progress = []
        inventory = SimpleNamespace(archive_members=("Content/People/file.duf",))

        with (
            mock.patch.object(packaging_utils.subprocess, "Popen", return_value=Process()),
            mock.patch.object(packaging_utils.queue, "Queue", return_value=OutputQueue()),
            mock.patch.object(packaging_utils.threading, "Thread", ReaderThread),
            mock.patch.object(
                packaging_utils.time,
                "monotonic",
                side_effect=(0, 4, 8, 12, 13),
            ),
            mock.patch.object(
                packaging_utils, "SEVEN_ZIP_PROGRESS_TIMEOUT_SECONDS", 5
            ),
        ):
            pipeline._zip_with_7z(
                self.destination / "package.zip",
                self.content_dir,
                inventory,
                progress.append,
            )

        self.assertEqual(progress, [10, 100])
        self.assertEqual(events, ["join", "close"])

    def test_public_validation_is_side_effect_free_and_checks_daz_roots(self):
        spec = self._spec()
        source_before = self._snapshot(self.build_dir)

        output_path = validate_package_spec(spec, ["People", "Runtime"])

        self.assertEqual(Path(output_path), self._output_path(spec).resolve())
        self.assertEqual(source_before, self._snapshot(self.build_dir))
        self.assertEqual(list(self.destination.iterdir()), [])

        with self.assertRaisesRegex(PackagingError, "recognized DAZ root"):
            validate_package_spec(spec, ["Runtime"])

    def test_empty_recognized_root_is_not_packageable_content(self):
        (self.content_dir / "People" / "Example" / "Product.duf").unlink()
        (self.content_dir / "Runtime").mkdir()
        self._write("README.txt", b"not DAZ content")

        with self.assertRaisesRegex(PackagingError, "recognized DAZ root"):
            validate_package_spec(self._spec(), ["People", "Runtime"])

    def test_pipeline_stages_without_mutating_source_and_verifies_members(self):
        self._write("Thumbs.db", b"junk")
        self._write("Runtime/Support/old.dsx", b"old support")
        self._write("People/Über Café.duf", b"unicode")
        image_path = self.root / "cover.png"
        Image.new("RGBA", (600, 400), (255, 0, 0, 128)).save(image_path)
        source_before = self._snapshot(self.build_dir)
        pipeline = self._pipeline(
            image_path=str(image_path),
            clean_support=True,
        )

        result = pipeline.execute(progress=lambda *_: None)

        self.assertIsInstance(result, PackageResult)
        self.assertEqual(result.status, PackageStatus.SUCCESS)
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Packaging complete.")
        self.assertEqual(source_before, self._snapshot(self.build_dir))
        self.assertIsNotNone(result.final_path)
        output_path = Path(result.final_path)
        self.assertGreater(result.file_size, 0)
        self.assertEqual(result.file_size, output_path.stat().st_size)

        with zipfile.ZipFile(output_path) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            manifest = ElementTree.fromstring(archive.read("Manifest.dsx"))
            supplement = ElementTree.fromstring(archive.read("Supplement.dsx"))
            manifest_names = {
                element.attrib["VALUE"]
                for element in manifest.findall("File")
            }

        content_names = {name for name in names if name.startswith("Content/")}
        self.assertEqual(manifest_names, content_names)
        self.assertIsNone(supplement.find("ProductStoreIDX"))
        self.assertNotIn("Content/Thumbs.db", names)
        self.assertNotIn("Content/Runtime/Support/old.dsx", names)
        self.assertIn("Content/People/Über Café.duf", names)
        expected_cover = build_support_cover_filename(
            "Renderotica",
            "70127",
            "Temple Ruins",
        )
        self.assertIn(f"Content/Runtime/Support/{expected_cover}", names)
        self.assertEqual(names - content_names, {"Manifest.dsx", "Supplement.dsx"})

    def test_reserved_daz_prefix_keeps_the_numeric_product_store_idx(self):
        result = self._pipeline(prefix="IM", store="DAZ 3D").execute()

        self.assertTrue(result.success, result.message)
        with zipfile.ZipFile(result.final_path) as archive:
            supplement = ElementTree.fromstring(archive.read("Supplement.dsx"))
        self.assertEqual(
            supplement.find("ProductStoreIDX").attrib["VALUE"],
            "70127-1",
        )

    def test_approved_replacement_removes_stale_members(self):
        spec = self._spec(replace_existing=True)
        final_path = self._output_path(spec)
        with zipfile.ZipFile(final_path, "w") as archive:
            archive.writestr("stale.txt", "old")

        pipeline = PackagingPipeline(spec)
        pipeline.seven_zip_path = None
        result = pipeline.execute()

        self.assertTrue(result.success)
        with zipfile.ZipFile(final_path) as archive:
            self.assertNotIn("stale.txt", archive.namelist())

    def test_unapproved_existing_output_is_preserved(self):
        spec = self._spec()
        final_path = self._output_path(spec)
        with zipfile.ZipFile(final_path, "w") as archive:
            archive.writestr("old.txt", "preserve me")
        original = final_path.read_bytes()
        pipeline = PackagingPipeline(spec)
        pipeline.seven_zip_path = None

        result = pipeline.execute()

        self.assertFalse(result.success)
        self.assertIn("not approved", result.message)
        self.assertEqual(final_path.read_bytes(), original)

    def test_output_appearing_after_validation_is_not_replaced(self):
        spec = self._spec()
        final_path = self._output_path(spec)
        pipeline = PackagingPipeline(spec)
        pipeline.seven_zip_path = None
        real_verify = pipeline._verify_archive

        def verify_then_create_output(*args, **kwargs):
            real_verify(*args, **kwargs)
            final_path.write_bytes(b"concurrent output")

        with mock.patch.object(
            pipeline,
            "_verify_archive",
            side_effect=verify_then_create_output,
        ):
            result = pipeline.execute()

        self.assertFalse(result.success)
        self.assertIn("not approved", result.message)
        self.assertEqual(final_path.read_bytes(), b"concurrent output")

    def test_failed_verification_preserves_existing_archive(self):
        spec = self._spec(replace_existing=True)
        final_path = self._output_path(spec)
        with zipfile.ZipFile(final_path, "w") as archive:
            archive.writestr("old.txt", "valid old archive")
        old_bytes = final_path.read_bytes()
        pipeline = PackagingPipeline(spec)
        pipeline.seven_zip_path = None

        with mock.patch.object(
            pipeline,
            "_verify_archive",
            side_effect=PackagingError("simulated verification failure"),
        ):
            result = pipeline.execute()

        self.assertFalse(result.success)
        self.assertEqual(final_path.read_bytes(), old_bytes)
        self.assertFalse(any(self.destination.glob(".dimcreator-package-*")))

    def test_failed_atomic_replace_preserves_existing_archive(self):
        spec = self._spec(replace_existing=True)
        final_path = self._output_path(spec)
        with zipfile.ZipFile(final_path, "w") as archive:
            archive.writestr("old.txt", "valid old archive")
        old_bytes = final_path.read_bytes()
        pipeline = PackagingPipeline(spec)
        pipeline.seven_zip_path = None

        with mock.patch("packaging_utils.os.replace", side_effect=PermissionError("locked")):
            result = pipeline.execute()

        self.assertFalse(result.success)
        self.assertEqual(final_path.read_bytes(), old_bytes)

    def test_destination_inside_build_tree_is_rejected(self):
        unsafe_destination = self.build_dir / "Output"
        unsafe_destination.mkdir()
        pipeline = self._pipeline(destination_folder=str(unsafe_destination))

        result = pipeline.execute()

        self.assertFalse(result.success)
        self.assertIn("inside the build directory", result.message)
        self.assertEqual(list(unsafe_destination.iterdir()), [])

    def test_destination_in_sibling_build_or_builds_root_is_rejected(self):
        sibling_destination = self.builds_dir / "Build2" / "Content"
        sibling_destination.mkdir(parents=True)
        arbitrary_builds_destination = self.builds_dir / "Output"
        arbitrary_builds_destination.mkdir()

        for unsafe_destination in (
            sibling_destination,
            self.builds_dir,
            arbitrary_builds_destination,
        ):
            with self.subTest(destination=unsafe_destination):
                pipeline = self._pipeline(
                    destination_folder=str(unsafe_destination)
                )

                result = pipeline.execute()

                self.assertFalse(result.success)
                self.assertIn("inside the build directory", result.message)
                self.assertFalse(
                    any(
                        path.suffix.casefold() == ".zip"
                        for path in unsafe_destination.iterdir()
                    )
                )

    def test_reparse_build_ancestor_is_rejected_as_a_source(self):
        build_root = Path(os.path.abspath(self.build_dir))
        real_check = packaging_utils._path_is_link_or_reparse

        def mark_build_as_reparse(path):
            if Path(path) == build_root:
                return True
            return real_check(path)

        with mock.patch.object(
            packaging_utils,
            "_path_is_link_or_reparse",
            side_effect=mark_build_as_reparse,
        ):
            result = self._pipeline().execute()

        self.assertFalse(result.success)
        self.assertIn("link or reparse point", result.message)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_non_writable_destination_is_rejected_before_staging(self):
        pipeline = self._pipeline()
        with mock.patch("packaging_utils.os.access", return_value=False):
            result = pipeline.execute()
        self.assertFalse(result.success)
        self.assertIn("not writable", result.message)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_cancelled_package_does_not_replace_existing_archive(self):
        spec = self._spec()
        final_path = self._output_path(spec)
        with zipfile.ZipFile(final_path, "w") as archive:
            archive.writestr("old.txt", "valid old archive")
        old_bytes = final_path.read_bytes()
        pipeline = PackagingPipeline(spec)
        pipeline.seven_zip_path = None

        result = pipeline.execute(is_cancelled=lambda: True)

        self.assertTrue(result.cancelled)
        self.assertEqual(result.status, PackageStatus.CANCELLED)
        self.assertEqual(final_path.read_bytes(), old_bytes)

    def test_cancellation_during_zip_is_cooperative_and_atomic(self):
        spec = self._spec()
        final_path = self._output_path(spec)
        with zipfile.ZipFile(final_path, "w") as archive:
            archive.writestr("old.txt", "valid old archive")
        old_bytes = final_path.read_bytes()
        pipeline = PackagingPipeline(spec)
        pipeline.seven_zip_path = None
        cancellation = {"requested": False}

        def progress(_percent, stage):
            if stage == "Packaging":
                cancellation["requested"] = True

        result = pipeline.execute(
            progress=progress,
            is_cancelled=lambda: cancellation["requested"],
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(final_path.read_bytes(), old_bytes)
        self.assertFalse(any(self.destination.glob(".dimcreator-package-*")))

    def test_invalid_session_name_fields_are_rejected_before_writing(self):
        for changes in (
            {"prefix": "../RE"},
            {"sku": "../70127"},
            {"product_part": 100},
            {"product_tags": "Plugin"},
            {"replace_existing": 1},
        ):
            with self.subTest(changes=changes):
                result = self._pipeline(**changes).execute()
                self.assertFalse(result.success)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_batch_detects_duplicate_case_insensitive_output_paths(self):
        first = self._spec(product_name="Product")
        second = self._spec(product_name="product")
        builds = [
            (SimpleNamespace(part=1, product_name="Product"), first),
            (SimpleNamespace(part=1, product_name="product"), second),
        ]
        worker = BatchPackagingWorker(builds)
        self.assertEqual(worker._duplicate_output_indices(), {0, 1})

    def test_batch_failure_emits_an_empty_string_for_a_missing_output_path(self):
        first = self._spec(product_name="Product")
        second = self._spec(product_name="product")
        builds = [
            (SimpleNamespace(part=1, product_name="Product"), first),
            (SimpleNamespace(part=1, product_name="product"), second),
        ]
        worker = BatchPackagingWorker(builds)
        completed = []
        worker.buildCompleted.connect(lambda *args: completed.append(args))

        worker.run()

        self.assertEqual(len(completed), 2)
        self.assertTrue(all(result[4] == "" for result in completed))

    @unittest.skipUnless(find_7z_executable(), "7-Zip is not installed")
    def test_real_7z_backend_uses_the_same_verified_inventory(self):
        spec = self._spec(replace_existing=True)
        final_path = self._output_path(spec)
        with zipfile.ZipFile(final_path, "w") as archive:
            archive.writestr("stale.txt", "must not survive")
        self._write("Thumbs.db", b"must not be listed")
        self._write("People/Über Café.duf", b"unicode through 7-Zip")
        pipeline = PackagingPipeline(spec)
        self.assertIsNotNone(pipeline.seven_zip_path)

        result = pipeline.execute()

        self.assertTrue(result.success, result.message)
        with zipfile.ZipFile(result.final_path) as archive:
            manifest = ElementTree.fromstring(archive.read("Manifest.dsx"))
            manifest_names = {
                element.attrib["VALUE"]
                for element in manifest.findall("File")
            }
            content_names = {
                name for name in archive.namelist() if name.startswith("Content/")
            }
            self.assertNotIn("stale.txt", archive.namelist())
            self.assertNotIn("Content/Thumbs.db", archive.namelist())
            self.assertIn("Content/People/Über Café.duf", archive.namelist())
        self.assertEqual(manifest_names, content_names)


if __name__ == "__main__":
    unittest.main()
