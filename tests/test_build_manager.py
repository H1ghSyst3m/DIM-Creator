import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from build_manager import create_build, delete_build, validate_build
from session import Build, Session, create_default_session


def make_build(number: int, part: int) -> Build:
    return Build(
        id=f"build_{number:03d}",
        folder=f"Build{number:03d}",
        part=part,
        guid=str(uuid.uuid4()),
    )


class BuildLifecycleTests(unittest.TestCase):
    @patch("build_manager.create_build_folder")
    def test_create_recalculates_stale_next_number(self, create_folder):
        session = create_default_session()
        session.next_build_number = 999

        build = create_build(session)

        self.assertEqual(build.id, "build_002")
        self.assertEqual(build.folder, "Build002")
        self.assertEqual(build.part, 2)
        create_folder.assert_called_once_with("Build002")

    @patch("build_manager.create_build_folder")
    def test_create_rejects_more_than_99_active_builds(self, create_folder):
        session = Session(
            builds=[make_build(number, number) for number in range(1, 100)]
        )
        with self.assertRaises(ValueError):
            create_build(session)
        create_folder.assert_not_called()

    @patch("build_manager.create_build_folder", side_effect=OSError("disk full"))
    def test_create_does_not_mutate_session_when_folder_creation_fails(self, _):
        session = create_default_session()
        before = list(session.builds)
        with self.assertRaises(OSError):
            create_build(session)
        self.assertEqual(session.builds, before)

    @patch("build_manager.delete_build_folder")
    def test_delete_selected_build_falls_back_to_first_id(self, _):
        session = Session(builds=[make_build(1, 1), make_build(2, 2)])
        session.last_selected_build_id = "build_002"
        delete_build(session, "build_002")
        self.assertEqual(session.last_selected_build_id, "build_001")
        self.assertEqual(session.next_build_number, 2)

    @patch("build_manager.create_build_folder")
    @patch("build_manager.delete_build_folder")
    def test_delete_only_build_selects_the_replacement(self, _, create_folder):
        session = create_default_session()
        delete_build(session, "build_001")

        self.assertEqual(len(session.builds), 1)
        self.assertEqual(session.last_selected_build_id, session.builds[0].id)
        session.validate()
        create_folder.assert_called_once()


class BuildValidationTests(unittest.TestCase):
    def test_empty_recognized_directory_is_not_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "Runtime"
            runtime.mkdir()
            build = make_build(1, 1)
            self.assertEqual(validate_build(build, temp_dir, ["Runtime"]), "empty")

            (runtime / "Thumbs.db").write_bytes(b"system")
            self.assertEqual(validate_build(build, temp_dir, ["Runtime"]), "empty")

    def test_valid_content_requires_official_metadata_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "Runtime"
            runtime.mkdir()
            (runtime / "content.duf").write_text("content", encoding="utf-8")
            build = make_build(1, 1)

            valid = {
                "store": "LOCAL USER",
                "product_name": "Product",
                "prefix": "LOCAL",
                "sku": "1",
                "tags": "DAZStudio4_5",
            }
            self.assertEqual(
                validate_build(build, temp_dir, ["Runtime"], valid), "ready"
            )

            for key, value in (
                ("prefix", "3DX"),
                ("prefix", "TOO-LONG"),
                ("sku", "0"),
                ("sku", "abc"),
                ("tags", "Plugin"),
                ("tags", "Software"),
            ):
                invalid = dict(valid)
                invalid[key] = value
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        validate_build(build, temp_dir, ["Runtime"], invalid),
                        "incomplete",
                    )


if __name__ == "__main__":
    unittest.main()
