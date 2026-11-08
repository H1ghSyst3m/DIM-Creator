import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import utils


class ArchiveToolDiscoveryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Program Files discovery is Windows-only")
    def test_program_files_7zip_is_preferred_to_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            program_files = root / "Program Files"
            trusted = program_files / "7-Zip" / "7z.exe"
            path_tool = root / "tools" / "7z.exe"
            trusted.parent.mkdir(parents=True)
            path_tool.parent.mkdir()
            trusted.write_bytes(b"trusted")
            path_tool.write_bytes(b"path")

            with (
                patch.object(utils, "_program_files_roots", return_value=[str(program_files)]),
                patch.dict(os.environ, {"PATH": str(path_tool.parent)}),
            ):
                discovered = utils.find_7z_executable()

            self.assertEqual(discovered, os.path.realpath(trusted))

    def test_current_directory_is_not_searched_via_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planted = root / ("7z.exe" if os.name == "nt" else "7z")
            planted.write_bytes(b"not trusted")
            if os.name != "nt":
                planted.chmod(0o755)

            with (
                patch.object(utils, "_program_files_roots", return_value=[]),
                patch.object(utils.os, "getcwd", return_value=str(root)),
                patch.dict(os.environ, {"PATH": str(root)}),
            ):
                self.assertIsNone(utils.find_7z_executable())

    def test_explicit_path_below_current_directory_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tool_dir = root / "tools"
            tool_dir.mkdir()
            tool = tool_dir / ("UnRAR.exe" if os.name == "nt" else "unrar")
            tool.write_bytes(b"trusted")
            if os.name != "nt":
                tool.chmod(0o755)

            with (
                patch.object(utils, "_program_files_roots", return_value=[]),
                patch.object(utils.os, "getcwd", return_value=str(root)),
                patch.dict(os.environ, {"PATH": str(tool_dir)}),
            ):
                discovered = utils.find_unrar_executable()

            self.assertEqual(discovered, os.path.realpath(tool))

    def test_relative_and_empty_path_entries_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tool_dir = root / "tools"
            tool_dir.mkdir()
            tool = tool_dir / ("UnRAR.exe" if os.name == "nt" else "unrar")
            tool.write_bytes(b"trusted")
            if os.name != "nt":
                tool.chmod(0o755)
            unsafe_path = os.pathsep.join(("", ".", "relative", str(tool_dir)))

            with (
                patch.object(utils, "_program_files_roots", return_value=[]),
                patch.dict(os.environ, {"PATH": unsafe_path}),
            ):
                discovered = utils.find_unrar_executable()

            self.assertEqual(discovered, os.path.realpath(tool))


class ManagedBuildPathTests(unittest.TestCase):
    def test_cleanup_preflights_reparse_points_before_deleting_anything(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            builds = Path(temp_dir) / "Builds"
            build = builds / "Build001"
            content = build / "Content"
            unsafe = content / "unsafe"
            unsafe.mkdir(parents=True)
            keep = content / "keep.duf"
            keep.write_bytes(b"keep")
            real_reparse_check = utils.has_reparse_point

            def mark_unsafe(path):
                if Path(path).name == "unsafe":
                    return True
                return real_reparse_check(path)

            with (
                patch.object(utils, "BUILDS_DIR", str(builds)),
                patch.object(utils, "has_reparse_point", side_effect=mark_unsafe),
            ):
                with self.assertRaisesRegex(OSError, "reparse"):
                    utils.clean_build_content("Build001")

            self.assertTrue(keep.is_file())
            self.assertTrue(unsafe.is_dir())

    def test_safe_cleanup_recreates_an_empty_content_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            builds = Path(temp_dir) / "Builds"
            content = builds / "Build001" / "Content"
            content.mkdir(parents=True)
            (content / "file.duf").write_bytes(b"content")

            with patch.object(utils, "BUILDS_DIR", str(builds)):
                utils.clean_build_content("Build001")

            self.assertTrue(content.is_dir())
            self.assertEqual(list(content.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
