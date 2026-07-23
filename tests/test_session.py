import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from session import (
    Build,
    Session,
    SessionError,
    SessionRecoveryError,
    SessionSaveError,
    UnsupportedSessionVersionError,
    create_default_session,
    delete_session_artifacts,
    load_session_result,
    save_session,
)


def make_build(number: int, part: int | None = None) -> Build:
    return Build(
        id=f"build_{number:03d}",
        folder=f"Build{number:03d}",
        part=number if part is None else part,
        guid=str(uuid.uuid4()),
    )


class SessionSchemaTests(unittest.TestCase):
    def test_v1_migrates_selection_to_id_and_recalculates_next_number(self):
        data = {
            "version": 1,
            "last_selected_build": 1,
            "next_build_number": 500,
            "unknown": "ignored",
            "builds": [
                {**make_build(1).to_dict(), "unknown": True},
                make_build(2).to_dict(),
            ],
        }

        session = Session.from_dict(data)

        self.assertEqual(session.version, 2)
        self.assertEqual(session.last_selected_build_id, "build_002")
        self.assertEqual(session.next_build_number, 3)
        self.assertFalse(hasattr(session, "last_selected_build"))
        self.assertNotIn("unknown", session.to_dict())
        self.assertNotIn("last_selected_build", session.to_dict())

    def test_unknown_build_fields_are_ignored(self):
        data = make_build(1).to_dict()
        data["future_field"] = {"anything": True}
        self.assertFalse(hasattr(Build.from_dict(data), "future_field"))

    def test_future_session_version_is_rejected(self):
        data = create_default_session().to_dict()
        data["version"] = 99
        with self.assertRaises(UnsupportedSessionVersionError):
            Session.from_dict(data)

    def test_build_path_identifiers_and_field_types_are_strict(self):
        cases = []
        traversal = make_build(1).to_dict()
        traversal["folder"] = "../Build001"
        cases.append(traversal)
        zero = make_build(1).to_dict()
        zero.update(id="build_000", folder="Build000")
        cases.append(zero)
        bad_part = make_build(1).to_dict()
        bad_part["part"] = True
        cases.append(bad_part)
        bad_guid = make_build(1).to_dict()
        bad_guid["guid"] = "not-a-guid"
        cases.append(bad_guid)
        bad_checked = make_build(1).to_dict()
        bad_checked["checked"] = 1
        cases.append(bad_checked)

        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(SessionError):
                    Build.from_dict(data)

    def test_session_rejects_duplicate_ids_and_non_contiguous_parts(self):
        duplicate = Session(builds=[make_build(1), make_build(1, part=2)])
        with self.assertRaises(SessionError):
            duplicate.validate()

        parts = Session(builds=[make_build(1), make_build(2, part=3)])
        with self.assertRaises(SessionError):
            parts.validate()

    def test_session_rejects_more_than_99_builds(self):
        data = create_default_session().to_dict()
        data["builds"] = [make_build(i, min(i, 99)).to_dict() for i in range(1, 101)]
        with self.assertRaises(SessionError):
            Session.from_dict(data)

    def test_v1_content_tags_are_preserved_for_compatibility(self):
        for tag in ("Plugin", "Software", "Win64,Plugin"):
            data = make_build(1).to_dict()
            data["tags"] = tag
            with self.subTest(tag=tag):
                self.assertEqual(Build.from_dict(data).tags, tag)


class SessionPersistenceTests(unittest.TestCase):
    def test_save_is_v2_atomic_and_uses_unique_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            session = create_default_session()
            save_session(session, str(path))
            session.builds[0].product_name = "Second"
            save_session(session, str(path))
            session.builds[0].product_name = "Third"
            save_session(session, str(path))

            saved = json.loads(path.read_text(encoding="utf-8"))
            backups = list((path.parent / "backups").glob("*.json"))
            self.assertEqual(saved["version"], 2)
            self.assertEqual(saved["last_selected_build_id"], "build_001")
            self.assertEqual(len(backups), 2)
            self.assertEqual(len({backup.name for backup in backups}), 2)

    def test_session_backup_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            session = create_default_session()
            save_session(session, str(path))
            for number in range(12):
                session.builds[0].product_name = str(number)
                save_session(session, str(path))
            self.assertEqual(len(list((path.parent / "backups").glob("*.json"))), 10)

    def test_failed_replace_preserves_existing_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            session = create_default_session()
            save_session(session, str(path))
            original = path.read_bytes()
            session.builds[0].product_name = "Changed"

            with patch("session.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(SessionSaveError):
                    save_session(session, str(path))

            self.assertEqual(path.read_bytes(), original)

    def test_corrupt_primary_is_quarantined_and_newest_valid_backup_restored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            backup_dir = path.parent / "backups"
            backup_dir.mkdir()
            older = create_default_session()
            older.builds[0].product_name = "Older"
            newer = create_default_session()
            newer.builds[0].product_name = "Newer"
            older_path = backup_dir / "session_older.json"
            newer_path = backup_dir / "session_newer.json"
            foreign = create_default_session()
            foreign.builds[0].product_name = "Foreign"
            foreign_path = backup_dir / "another-session.json"
            older_path.write_text(json.dumps(older.to_dict()), encoding="utf-8")
            newer_path.write_text(json.dumps(newer.to_dict()), encoding="utf-8")
            foreign_path.write_text(json.dumps(foreign.to_dict()), encoding="utf-8")
            os.utime(older_path, (1, 1))
            os.utime(newer_path, (2, 2))
            os.utime(foreign_path, (3, 3))
            path.write_text("{broken", encoding="utf-8")

            result = load_session_result(str(path))

            self.assertEqual(result.source, "backup")
            self.assertEqual(result.session.builds[0].product_name, "Newer")
            self.assertTrue(list(path.parent.glob("session.corrupt-*.json")))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["builds"][0]["product_name"],
                "Newer",
            )

    def test_missing_primary_ignores_foreign_json_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            backup_dir = path.parent / "backups"
            backup_dir.mkdir()
            foreign = backup_dir / "other.json"
            foreign.write_text(
                json.dumps(create_default_session().to_dict()),
                encoding="utf-8",
            )

            result = load_session_result(str(path))

            self.assertEqual(result.source, "new")
            self.assertIsNone(result.session)
            self.assertFalse(path.exists())

    def test_explicit_cleanup_removes_primary_and_its_backups_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            session = create_default_session()
            save_session(session, str(path))
            session.builds[0].product_name = "Changed"
            save_session(session, str(path))
            backup_dir = path.parent / "backups"
            foreign = backup_dir / "other.json"
            foreign.write_text("{}", encoding="utf-8")
            quarantine = path.parent / "session.corrupt-kept.json"
            quarantine.write_text("broken", encoding="utf-8")

            delete_session_artifacts(path, include_backups=True)

            self.assertFalse(path.exists())
            self.assertEqual(list(backup_dir.glob("session_*.json")), [])
            self.assertTrue(foreign.exists())
            self.assertTrue(quarantine.exists())
            self.assertEqual(load_session_result(str(path)).source, "new")

    def test_corrupt_primary_without_backup_requires_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            path.write_text("[]", encoding="utf-8")

            with self.assertRaises(SessionRecoveryError):
                load_session_result(str(path))

            self.assertFalse(path.exists())
            self.assertTrue(list(path.parent.glob("session.corrupt-*.json")))

    def test_future_primary_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            data = create_default_session().to_dict()
            data["version"] = 99
            original = json.dumps(data)
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(UnsupportedSessionVersionError):
                load_session_result(str(path))

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse(list(path.parent.glob("session.corrupt-*.json")))

    def test_save_refuses_to_overwrite_invalid_primary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(SessionSaveError):
                save_session(create_default_session(), str(path))
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")


if __name__ == "__main__":
    unittest.main()
