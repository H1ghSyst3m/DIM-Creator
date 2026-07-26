import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config_utils
from config_utils import (
    CURRENT_CONFIG_VERSION,
    ConfigError,
    UnsupportedConfigVersionError,
    atomic_write_json,
    load_configurations,
    normalize_store_items,
    normalize_tag_items,
    update_configuration,
)
from naming_utils import DIM_PREFIX_PATTERN


class ConfigurationMigrationTests(unittest.TestCase):
    def test_prefix_pattern_is_shared_with_naming_utils(self):
        self.assertIs(config_utils.DIM_PREFIX_PATTERN, DIM_PREFIX_PATTERN)
        self.assertFalse(hasattr(config_utils, "_PREFIX_RE"))

    def test_defaults_use_dim_compatible_prefixes_and_content_tags(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stores, prefixes, tags, _ = load_configurations(temp_dir)

            self.assertIn("3DExport", stores)
            self.assertEqual(prefixes["3DExport"], "D3X")
            self.assertEqual(prefixes["LOCAL USER"], "LOCAL")
            self.assertIn("3dsMax", tags)
            self.assertIn("DAZStudio5", tags)
            self.assertIn("Blender", tags)
            self.assertIn("Cinema4D", tags)
            self.assertIn("Maya", tags)
            self.assertIn("Unity", tags)
            self.assertIn("Unreal", tags)
            self.assertIn("PublishingBuild", tags)
            self.assertIn("CloudAvailable", tags)
            self.assertIn("CloudInstalled", tags)
            self.assertIn("Lightwave", tags)
            self.assertNotIn("LightWave", tags)
            self.assertNotIn("Plugin", tags)
            self.assertNotIn("Software", tags)
            self.assertGreaterEqual(CURRENT_CONFIG_VERSION, 2)

    def test_all_daz_reserved_prefixes_warn_for_third_party_stores(self):
        with self.assertLogs("DIMCreator.config_utils", level="WARNING") as captured:
            items = normalize_store_items(
                [
                    {"name": f"Custom {prefix}", "prefix": prefix}
                    for prefix in ("IM", "DZ", "DAZ", "DAZ3D", "TAFI")
                ]
            )

        self.assertEqual(len(items), 5)
        messages = "\n".join(captured.output)
        for prefix in ("IM", "DZ", "DAZ", "DAZ3D", "TAFI"):
            self.assertIn(f"reserved {prefix} prefix", messages)

    def test_v1_store_migration_repairs_known_prefixes_and_preserves_valid_custom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "store_data.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "data": [
                            {"name": "3DExport", "prefix": "3DX"},
                            {"name": "LOCAL USER", "prefix": "IM"},
                            {"name": "Custom", "prefix": "CUS"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            defaults = {
                "version": CURRENT_CONFIG_VERSION,
                "data": [
                    {"name": "3DExport", "prefix": "D3X"},
                    {"name": "LOCAL USER", "prefix": "LOCAL"},
                ],
            }

            items = update_configuration(
                str(path), defaults, CURRENT_CONFIG_VERSION, True
            )
            by_name = {item["name"]: item["prefix"] for item in items}

            self.assertEqual(by_name["3DExport"], "D3X")
            self.assertEqual(by_name["LOCAL USER"], "LOCAL")
            self.assertEqual(by_name["Custom"], "CUS")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["version"],
                CURRENT_CONFIG_VERSION,
            )

    def test_tag_migration_is_case_insensitive_and_removes_non_content_types(self):
        tags = normalize_tag_items(
            ["LightWave", "lightwave", "Plugin", "Software", "DAZStudio5"]
        )
        self.assertEqual(tags, ["Lightwave", "DAZStudio5"])

    def test_malformed_current_version_items_are_repaired_without_sort_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "Config"
            config_dir.mkdir()
            (config_dir / "product_tags.json").write_text(
                json.dumps(
                    {
                        "version": CURRENT_CONFIG_VERSION,
                        "data": ["DAZStudio4_5", {"bad": True}, None, 3],
                    }
                ),
                encoding="utf-8",
            )
            _, _, tags, _ = load_configurations(temp_dir)
            self.assertIn("DAZStudio4_5", tags)
            self.assertTrue(all(isinstance(tag, str) for tag in tags))

    def test_current_version_keeps_intentional_user_deletions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "product_tags.json"
            path.write_text(
                json.dumps(
                    {"version": CURRENT_CONFIG_VERSION, "data": ["General"]}
                ),
                encoding="utf-8",
            )
            items = update_configuration(
                str(path),
                {
                    "version": CURRENT_CONFIG_VERSION,
                    "data": ["General", "DAZStudio5"],
                },
                CURRENT_CONFIG_VERSION,
                False,
            )
            self.assertEqual(items, ["General"])

    def test_corrupt_config_is_quarantined_before_defaults_are_written(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "product_tags.json"
            path.write_text("{bad", encoding="utf-8")
            defaults = {"version": CURRENT_CONFIG_VERSION, "data": ["General"]}

            self.assertEqual(
                update_configuration(
                    str(path), defaults, CURRENT_CONFIG_VERSION, False
                ),
                ["General"],
            )
            self.assertTrue(list(path.parent.glob("product_tags.corrupt-*.json")))

    def test_corrupt_config_restores_latest_valid_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "product_tags.json"
            backup_dir = path.parent / "backups"
            backup_dir.mkdir()
            (backup_dir / "product_tags_saved.json").write_text(
                json.dumps(
                    {
                        "version": CURRENT_CONFIG_VERSION,
                        "data": ["CustomTag"],
                    }
                ),
                encoding="utf-8",
            )
            path.write_text("{bad", encoding="utf-8")

            items = update_configuration(
                str(path),
                {"version": CURRENT_CONFIG_VERSION, "data": ["General"]},
                CURRENT_CONFIG_VERSION,
                False,
            )

            self.assertEqual(items, ["CustomTag"])
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["data"],
                ["CustomTag"],
            )

    def test_backup_recovery_skips_candidate_lost_during_stat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "product_tags.json"
            backup_dir = path.parent / "backups"
            backup_dir.mkdir()
            available = backup_dir / "product_tags_available.json"
            unavailable = backup_dir / "product_tags_unavailable.json"
            payload = {
                "version": CURRENT_CONFIG_VERSION,
                "data": ["Recovered"],
            }
            available.write_text(json.dumps(payload), encoding="utf-8")
            unavailable.write_text(json.dumps(payload), encoding="utf-8")
            real_stat = Path.stat

            def stat_with_race(candidate, *args, **kwargs):
                if candidate == unavailable:
                    raise FileNotFoundError(candidate)
                return real_stat(candidate, *args, **kwargs)

            with patch.object(Path, "stat", autospec=True, side_effect=stat_with_race):
                recovered = config_utils._load_latest_config_backup(
                    path, CURRENT_CONFIG_VERSION
                )

            self.assertEqual(recovered, (available, payload))

    def test_future_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "product_tags.json"
            original = json.dumps({"version": 99, "data": ["FutureTag"]})
            path.write_text(original, encoding="utf-8")

            items = update_configuration(
                str(path),
                {"version": CURRENT_CONFIG_VERSION, "data": ["General"]},
                CURRENT_CONFIG_VERSION,
                False,
            )

            self.assertEqual(items, ["FutureTag"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)


class ConfigurationPersistenceTests(unittest.TestCase):
    def test_atomic_writer_uses_unique_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            atomic_write_json(path, {"version": 2, "data": [1]})
            atomic_write_json(path, {"version": 2, "data": [2]})
            atomic_write_json(path, {"version": 2, "data": [3]})

            backups = list((path.parent / "backups").glob("*.json"))
            self.assertEqual(len(backups), 2)
            self.assertEqual(len({item.name for item in backups}), 2)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["data"], [3]
            )

    def test_config_backup_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            atomic_write_json(path, {"version": 2, "data": [0]})
            for number in range(12):
                atomic_write_json(path, {"version": 2, "data": [number]})
            self.assertEqual(len(list((path.parent / "backups").glob("*.json"))), 10)

    def test_atomic_writer_refuses_to_downgrade_future_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            original = json.dumps({"version": 99, "data": []})
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(UnsupportedConfigVersionError):
                atomic_write_json(path, {"version": 2, "data": []})
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_settings_validators_reject_invalid_prefix_and_application_tags(self):
        with self.assertRaises(ConfigError):
            normalize_store_items(
                [{"name": "Invalid", "prefix": "3DX"}], reject_invalid=True
            )
        for tag in ("Plugin", "Software"):
            with self.subTest(tag=tag), self.assertRaises(ConfigError):
                normalize_tag_items([tag], reject_unsupported=True)


if __name__ == "__main__":
    unittest.main()
