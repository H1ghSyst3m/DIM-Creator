import hashlib
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from subprocess import CalledProcessError, run
from unittest.mock import patch

import release


REF = "refs/tags/v3.0.0"
COMMIT = "a" * 40


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.write("Cargo.toml", '[workspace.package]\nversion = "3.0.0"\n')
        for crate in ("core", "src-tauri"):
            (self.root / crate).mkdir()
            self.write(f"{crate}/Cargo.toml", f'[package]\nname = "{crate}"\nversion.workspace = true\n')
        self.write("Cargo.lock", ''.join(
            f'[[package]]\nname = "{crate}"\nversion = "3.0.0"\n' for crate in ("core", "src-tauri")
        ))
        self.write("package.json", '{"version":"3.0.0"}')
        self.write("package-lock.json", '{"version":"3.0.0","packages":{"":{"version":"3.0.0"}}}')
        self.write("src-tauri/tauri.conf.json", '{"version":"../package.json"}')
        self.changelog = "# Changelog\n\n## [Unreleased]\n\nFuture\n\n## [3.0.0]\n\n### Added\n\n- v3 only\n"
        self.write("CHANGELOG.md", self.changelog)

    def write(self, path, content):
        (self.root / path).write_text(content, encoding="utf-8")

    def test_extracts_exact_tag_section(self):
        self.write("CHANGELOG.md", self.changelog + "\n## [3.0.1]\n\nOther version\n")
        self.assertEqual(release.metadata(self.root, REF), ("3.0.0", "### Added\n\n- v3 only\n"))

    def test_rejects_branch_legacy_and_noncanonical_tags(self):
        for ref in ("refs/heads/main", "refs/tags/v2.1.0", "refs/tags/v3.1",
                    "refs/tags/v3.01.0", "refs/tags/v3.0.0-rc.1", "refs/tags/v3.0.0+build"):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "release tag"):
                release.metadata(self.root, ref)

    def test_rejects_each_mismatched_version(self):
        changes = (
            ("package.json", b"3.0.0", b"3.0.1"),
            ("package-lock.json", b"3.0.0", b"3.0.1"),
            ("Cargo.toml", b"3.0.0", b"3.0.1"),
            ("core/Cargo.toml", b"version.workspace = true", b'version = "3.0.1"'),
            ("src-tauri/Cargo.toml", b"version.workspace = true", b'version = "3.0.1"'),
            ("Cargo.lock", b"3.0.0", b"3.0.1"),
            ("src-tauri/tauri.conf.json", b"../package.json", b"3.0.1"),
        )
        for name, before, after in changes:
            path = self.root / name
            old = path.read_bytes()
            try:
                path.write_bytes(old.replace(before, after))
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Version mismatch"):
                    release.metadata(self.root, REF)
            finally:
                path.write_bytes(old)

    def test_validates_effective_windows_version(self):
        self.write("src-tauri/tauri.windows.conf.json", '{"version":"3.0.1"}')
        with self.assertRaisesRegex(ValueError, "Tauri"):
            release.metadata(self.root, REF)

    def test_bump_updates_project_versions_and_keeps_dependency_records(self):
        dependency = '\n[[package]]\nname = "core"\nversion = "3.0.0"\nsource = "registry+https://example.test"\n'
        path = self.root / "Cargo.lock"
        path.write_text(path.read_text(encoding="utf-8") + dependency, encoding="utf-8")
        self.write("package-lock.json", json.dumps({
            "version": "3.0.0",
            "packages": {"": {"version": "3.0.0"}, "node_modules/sample": {"version": "3.0.0"}},
        }))
        unchanged = ("core/Cargo.toml", "src-tauri/Cargo.toml", "src-tauri/tauri.conf.json", "CHANGELOG.md")
        originals = {name: (self.root / name).read_bytes() for name in unchanged}

        result = run(
            [sys.executable, "-B", release.__file__, "bump", "3.0.1"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        for name, original in originals.items():
            self.assertEqual((self.root / name).read_bytes(), original)
        cargo = tomllib.loads(path.read_text(encoding="utf-8"))["package"]
        self.assertEqual([item["version"] for item in cargo], ["3.0.1", "3.0.1", "3.0.0"])
        self.assertTrue(path.read_text(encoding="utf-8").endswith(dependency))
        npm_lock = json.loads((self.root / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(npm_lock["packages"]["node_modules/sample"]["version"], "3.0.0")

        with self.assertRaisesRegex(ValueError, "changelog"):
            release.metadata(self.root, "refs/tags/v3.0.1")
        self.write("CHANGELOG.md", self.changelog + "\n## [3.0.1]\n\nExample release\n")
        self.assertEqual(release.metadata(self.root, "refs/tags/v3.0.1"), ("3.0.1", "Example release\n"))

    def test_bump_rejects_invalid_versions_before_writing(self):
        files = ("package.json", "package-lock.json", "Cargo.toml", "Cargo.lock")
        originals = {name: (self.root / name).read_bytes() for name in files}
        for version in ("3.0", "v3.0.1", "3.00.1", "3.0.1-rc.1", "4.0.0"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "version"):
                release.bump_version(self.root, version)
        self.assertEqual({name: (self.root / name).read_bytes() for name in files}, originals)

    def test_bump_preserves_recovery_copy_when_rollback_is_blocked(self):
        npm = self.root / "package.json"
        lock = self.root / "package-lock.json"
        original_npm, original_lock = npm.read_bytes(), lock.read_bytes()
        replace = Path.replace

        def replace_with_locked_files(source, target):
            if source.name in ("Cargo.toml", "package-lock.json.original"):
                raise PermissionError(f"{target.name} is locked")
            return replace(source, target)

        with patch.object(Path, "replace", autospec=True, side_effect=replace_with_locked_files):
            with self.assertRaisesRegex(OSError, "Could not restore package-lock.json"):
                release.bump_version(self.root, "3.0.1")

        self.assertEqual(npm.read_bytes(), original_npm)
        backup, = self.root.glob(".version-bump-*/package-lock.json.original")
        self.assertEqual(backup.read_bytes(), original_lock)

    def test_requires_one_nonempty_changelog_section(self):
        for changelog in (self.changelog.replace("[3.0.0]", "[3.0.1]"),
                          self.changelog + "\n## [3.0.0]\n\nDuplicate\n",
                          "# Changelog\n\n## [3.0.0]\n\n## [3.0.1]\n\nOther\n"):
            self.write("CHANGELOG.md", changelog)
            with self.subTest(changelog=changelog), self.assertRaisesRegex(ValueError, "changelog"):
                release.metadata(self.root, REF)

    @patch.object(release, "command")
    def test_checkout_requires_tag_commit_on_main(self, run):
        run.side_effect = [COMMIT, COMMIT, COMMIT, ""]
        self.assertEqual(release.validate_checkout(REF, COMMIT), COMMIT)
        run.assert_called_with("git", "merge-base", "--is-ancestor", COMMIT, "refs/remotes/origin/main")
        run.side_effect = [COMMIT, COMMIT, COMMIT, CalledProcessError(1, "git merge-base")]
        with self.assertRaises(CalledProcessError):
            release.validate_checkout(REF, COMMIT)
        run.side_effect = [COMMIT, COMMIT, "b" * 40]
        with self.assertRaisesRegex(ValueError, "checkout"):
            release.validate_checkout(REF, COMMIT)

    def installer(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        exe = bundle / "DIM Creator_3.0.0_x64-setup.exe"
        exe.write_bytes(b"MZ" + bytes(58) + bytes([64, 0, 0, 0]) + b"PE\0\0")
        return exe

    def test_creates_checksum_for_published_name(self):
        exe = self.installer()
        installer, sums = release.prepare_assets(exe.parent, self.root / "assets", "3.0.0")
        self.assertEqual(installer.name, "DIM-Creator_3.0.0_x64-setup.exe")
        self.assertEqual(installer.read_bytes(), exe.read_bytes())
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        self.assertEqual(sums.read_text(encoding="ascii"), f"{digest}  {installer.name}\n")

    def test_rejects_missing_empty_or_multiple_installers(self):
        exe = self.installer()
        data = exe.read_bytes()
        exe.unlink()
        with self.assertRaises(ValueError):
            release.prepare_assets(exe.parent, self.root / "assets", "3.0.0")
        exe.touch()
        with self.assertRaises(ValueError):
            release.prepare_assets(exe.parent, self.root / "assets", "3.0.0")
        exe.write_bytes(data)
        exe.with_name("other.exe").write_bytes(data)
        with self.assertRaises(ValueError):
            release.prepare_assets(exe.parent, self.root / "assets", "3.0.0")

    def release_fixture(self):
        exe = self.installer()
        assets = release.prepare_assets(exe.parent, self.root / "assets", "3.0.0")
        notes = self.root / "notes.md"
        notes.write_text("- v3 release\n", encoding="utf-8")
        draft = {
            "id": 123, "tag_name": "v3.0.0", "draft": True,
            "assets": [
                {"name": p.name, "size": p.stat().st_size, "state": "uploaded",
                 "digest": f"sha256:{hashlib.sha256(p.read_bytes()).hexdigest()}"}
                for p in assets
            ],
        }
        return assets, notes, draft

    @patch.object(release, "command")
    def test_publishes_only_verified_draft(self, run):
        assets, notes, draft = self.release_fixture()
        tag_object = "b" * 40
        remote_ref = f"{tag_object}\t{REF}\n{COMMIT}\t{REF}^{{}}"
        published = {**draft, "draft": False, "html_url": "https://example.test/releases/tag/v3.0.0"}
        run.side_effect = [remote_ref, "[]", "draft created", json.dumps([[draft]]), remote_ref, json.dumps(published)]
        self.assertEqual(release.publish(REF, COMMIT, notes, assets), published["html_url"])
        self.assertIn("--draft", run.call_args_list[2].args)
        self.assertIn("--verify-tag", run.call_args_list[2].args)
        self.assertIn("PATCH", run.call_args_list[-1].args)
        self.assertIn("draft=false", run.call_args_list[-1].args)

    @patch.object(release, "command")
    def test_existing_release_or_draft_prevents_creation(self, run):
        assets, notes, draft = self.release_fixture()
        for existing in (draft, {**draft, "draft": False}):
            run.reset_mock()
            run.side_effect = [f"{COMMIT}\t{REF}", json.dumps([[existing]])]
            with self.assertRaisesRegex(ValueError, "already exists"):
                release.publish(REF, COMMIT, notes, assets)
            self.assertEqual(run.call_count, 2)

    @patch.object(release, "command")
    def test_failed_upload_or_asset_verification_never_publishes(self, run):
        assets, notes, draft = self.release_fixture()
        failures = (
            [CalledProcessError(1, "gh release create")],
            ["draft created", json.dumps([[{**draft, "assets": draft["assets"][:1]}]])],
            ["draft created", json.dumps([[{**draft, "assets": [
                {**draft["assets"][0], "digest": "sha256:wrong"}, draft["assets"][1]
            ]}]])],
            ["draft created", json.dumps([[draft]]), f"{'b' * 40}\t{REF}"],
        )
        for responses in failures:
            run.reset_mock()
            run.side_effect = [f"{COMMIT}\t{REF}", "[]", *responses]
            with self.assertRaises((ValueError, CalledProcessError)):
                release.publish(REF, COMMIT, notes, assets)
            self.assertFalse(any("PATCH" in call.args for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
