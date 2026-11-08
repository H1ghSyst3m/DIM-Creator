"""Validate and publish the Windows release for an existing DIM Creator v3 tag."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPOSITORY = "H1ghSyst3m/DIM-Creator"
VERSION = re.compile(r"3\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
CRATE_MANIFESTS = ("core/Cargo.toml", "src-tauri/Cargo.toml")
NPM_VERSION_PATH = "../package.json"


def version_from_ref(ref):
    prefix = "refs/tags/v"
    version = ref.removeprefix(prefix)
    if version == ref or not VERSION.fullmatch(version):
        raise ValueError("Expected a stable v3 release tag (refs/tags/v3.MINOR.PATCH).")
    return version


def windows_tauri_config(root):
    tauri = root / "src-tauri"
    if any((tauri / name).exists() for name in ("tauri.windows.conf.json5", "Tauri.windows.toml")):
        raise ValueError("Release validation requires JSON for the Windows Tauri config.")
    config = json.loads((tauri / "tauri.conf.json").read_text(encoding="utf-8"))
    windows = tauri / "tauri.windows.conf.json"
    if windows.exists():
        config.update(json.loads(windows.read_text(encoding="utf-8")))
    return config


def with_toml_version(text, version):
    updated, count = re.subn(r'(?m)^([ \t]*version[ \t]*=[ \t]*")[^"\r\n]*"', rf'\g<1>{version}"', text)
    if count != 1:
        raise ValueError("Expected exactly one TOML version field to update.")
    return updated


def bump_version(root, version):
    if not VERSION.fullmatch(version):
        raise ValueError("Expected a stable version in the form 3.MINOR.PATCH, for example 3.0.1.")
    if windows_tauri_config(root).get("version") not in (None, NPM_VERSION_PATH):
        raise ValueError(f"Tauri must inherit its version from {NPM_VERSION_PATH} or Cargo.")

    crates = []
    for manifest in CRATE_MANIFESTS:
        package = tomllib.loads((root / manifest).read_text(encoding="utf-8"))["package"]
        if package["version"] != {"workspace": True}:
            raise ValueError(f"{manifest} must inherit the Cargo workspace version.")
        crates.append(package["name"])

    workspace = (root / "Cargo.toml").read_text(encoding="utf-8")
    updated_workspace = with_toml_version(workspace, version)
    if tomllib.loads(updated_workspace)["workspace"]["package"]["version"] != version:
        raise ValueError("Expected the version field in [workspace.package].")

    lock = (root / "Cargo.lock").read_text(encoding="utf-8")
    sections = re.split(r"(?m)(?=^\[\[package\]\])", lock)
    updated_crates = []
    for index, section in enumerate(sections[1:], 1):
        package, = tomllib.loads(section)["package"]
        if package["name"] in crates and "source" not in package:
            updated_crates.append(package["name"])
            sections[index] = with_toml_version(section, version)
    if sorted(updated_crates) != sorted(crates):
        raise ValueError("Expected one Cargo.lock entry for each workspace crate.")

    npm = json.loads((root / "package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    npm["version"] = npm_lock["version"] = npm_lock["packages"][""]["version"] = version
    updates = {
        "package.json": json.dumps(npm, indent=2, ensure_ascii=False) + "\n",
        "package-lock.json": json.dumps(npm_lock, indent=2, ensure_ascii=False) + "\n",
        "Cargo.toml": updated_workspace,
        "Cargo.lock": "".join(sections),
    }

    staged = Path(tempfile.mkdtemp(prefix=".version-bump-", dir=root.resolve()))
    preserve_backups = False
    try:
        for name, contents in updates.items():
            original = root / name
            shutil.copy2(original, staged / f"{name}.original")
            (staged / name).write_text(contents, encoding="utf-8", newline="\n")
        applied = []
        try:
            for name in updates:
                (staged / name).replace(root / name)
                applied.append(name)
        except OSError as error:
            unrestored = []
            for name in reversed(applied):
                try:
                    (staged / f"{name}.original").replace(root / name)
                except OSError:
                    unrestored.append(name)
            if unrestored:
                preserve_backups = True
                raise OSError(f"{error}. Could not restore {', '.join(unrestored)}. Backups are in {staged}.") from error
            raise
    finally:
        if not preserve_backups:
            shutil.rmtree(staged)


def metadata(root, ref):
    version = version_from_ref(ref)
    npm = json.loads((root / "package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    versions = {
        "package.json": npm["version"],
        "package-lock.json": npm_lock["version"],
        "package-lock.json (root package)": npm_lock["packages"][""]["version"],
    }
    cargo_lock = tomllib.loads((root / "Cargo.lock").read_text(encoding="utf-8"))
    workspace = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
    for manifest in CRATE_MANIFESTS:
        package = tomllib.loads((root / manifest).read_text(encoding="utf-8"))["package"]
        declared = package["version"]
        versions[manifest] = workspace["workspace"]["package"]["version"] if declared == {"workspace": True} else declared
        locked = [
            item["version"] for item in cargo_lock["package"]
            if item["name"] == package["name"] and "source" not in item
        ]
        versions[f"Cargo.lock ({package['name']})"] = locked[0] if len(locked) == 1 else locked

    configured = windows_tauri_config(root).get("version")
    if configured == NPM_VERSION_PATH:
        configured = npm["version"]
    elif configured is None:
        configured = versions["src-tauri/Cargo.toml"]
    versions["Tauri (effective Windows version)"] = configured

    for source, actual in versions.items():
        if actual != version:
            raise ValueError(f"Version mismatch: {source} has {actual!r}, tag requires {version}.")

    lines = (root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    matches = [i for i, line in enumerate(lines) if line.rstrip() == f"## [{version}]"]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one changelog heading '## [{version}]'.")
    start = matches[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    notes = "\n".join(lines[start:end]).strip()
    if not notes:
        raise ValueError(f"The changelog section for {version} is empty.")
    return version, notes + "\n"


def command(*args):
    return subprocess.check_output(args, encoding="utf-8").strip()


def validate_checkout(ref, event_sha):
    version_from_ref(ref)
    if not re.fullmatch(r"[0-9a-f]{40}", event_sha):
        raise ValueError("Expected the GitHub event SHA.")
    head = command("git", "rev-parse", "--verify", "HEAD")
    event_commit = command("git", "rev-parse", "--verify", f"{event_sha}^{{commit}}")
    tag_commit = command("git", "rev-parse", "--verify", f"{ref}^{{commit}}")
    if head != event_commit or head != tag_commit:
        raise ValueError("The checkout, tag and event must resolve to the same commit.")
    command("git", "merge-base", "--is-ancestor", head, "refs/remotes/origin/main")
    return head


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare_assets(bundle, destination, version):
    expected = bundle / f"DIM Creator_{version}_x64-setup.exe"
    if list(bundle.glob("*.exe")) != [expected] or not expected.is_file():
        raise ValueError(f"Expected exactly one NSIS installer named {expected.name}.")
    with expected.open("rb") as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError(f"Invalid Windows installer: {expected.name}.")
        source.seek(int.from_bytes(header[60:64], "little"))
        if source.read(4) != b"PE\0\0":
            raise ValueError(f"Invalid PE signature: {expected.name}.")
    destination.mkdir()
    installer = destination / f"DIM-Creator_{version}_x64-setup.exe"
    shutil.copyfile(expected, installer)
    sums = destination / "SHA256SUMS.txt"
    sums.write_text(f"{sha256(installer)}  {installer.name}\n", encoding="ascii", newline="\n")
    return installer, sums


def check_remote_tag(ref, commit):
    peeled = f"{ref}^{{}}"
    refs = dict(
        (name, sha) for sha, name in (
            row.split("\t") for row in command(
                "git", "ls-remote", "--exit-code", "--tags", f"https://github.com/{REPOSITORY}.git", ref, peeled
            ).splitlines()
        )
    )
    if ref not in refs or refs.get(peeled, refs[ref]) != commit:
        raise ValueError("The remote tag no longer points to the verified build commit.")


def releases_for_tag(tag):
    pages = json.loads(command("gh", "api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"))
    return [release for page in pages for release in page if release["tag_name"] == tag]


def publish(ref, commit, notes, files):
    version_from_ref(ref)
    tag = ref.removeprefix("refs/tags/")
    check_remote_tag(ref, commit)
    if releases_for_tag(tag):
        raise ValueError(f"A release or draft for {tag} already exists. Inspect it before retrying.")

    command(
        "gh", "release", "create", tag, *(str(path) for path in files),
        "--repo", REPOSITORY, "--verify-tag", "--draft",
        "--title", f"DIM Creator {tag}", "--notes-file", str(notes),
    )
    candidates = releases_for_tag(tag)
    if len(candidates) != 1 or candidates[0]["draft"] is not True:
        raise ValueError(f"Expected exactly one draft for {tag}.")
    draft = candidates[0]
    expected = sorted((p.name, p.stat().st_size, "uploaded", f"sha256:{sha256(p)}") for p in files)
    uploaded = sorted((a["name"], a["size"], a["state"], a.get("digest")) for a in draft["assets"])
    if uploaded != expected:
        raise ValueError("Uploaded assets differ from the verified installer and SHA-256 file. The release stays a draft.")

    check_remote_tag(ref, commit)
    published = json.loads(command(
        "gh", "api", "--method", "PATCH", f"repos/{REPOSITORY}/releases/{draft['id']}",
        "-F", "draft=false", "-f", "make_latest=legacy",
    ))
    if published["draft"] is not False or published["tag_name"] != tag:
        raise ValueError("GitHub did not confirm publication of the expected release.")
    return published["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    bump = subparsers.add_parser(
        "bump",
        usage="npm run version:bump -- VERSION",
        description="Set the DIM Creator version in the npm and Cargo manifests and lockfiles. "
                    "Tauri and the frontend read package.json. Dependency versions are preserved.",
        epilog="Add a non-empty '## [VERSION]' section to CHANGELOG.md yourself. "
               "Commit, tag (vVERSION) and push separately.",
    )
    bump.add_argument("version", metavar="VERSION", help="stable v3 version, for example 3.0.1")
    subparsers.add_parser("notes").add_argument("tag")
    subparsers.add_parser("validate")
    release = subparsers.add_parser("publish")
    release.add_argument("bundle", type=Path)
    release.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    if args.action == "bump":
        bump_version(root, args.version)
        print(f"Set DIM Creator project versions to {args.version}.")
        return
    if args.action == "notes":
        _, notes = metadata(root, f"refs/tags/{args.tag}")
        print(notes, end="")
        return

    if os.environ["GITHUB_REPOSITORY"] != REPOSITORY or os.environ["GITHUB_EVENT_NAME"] != "push":
        raise ValueError("Release operations require a push event in the official repository.")
    ref = os.environ["GITHUB_REF"]
    version, notes = metadata(root, ref)
    if args.action == "validate":
        commit = validate_checkout(ref, os.environ["GITHUB_SHA"])
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"commit={commit}\n")
        print(f"Validated {version} at {commit} on main.")
    else:
        commit = command("git", "rev-parse", "--verify", "HEAD")
        if commit != os.environ["RELEASE_COMMIT"]:
            raise ValueError("The publish checkout does not match the verified build.")
        files = prepare_assets(args.bundle, args.output, version)
        notes_file = args.output / "release-notes.md"
        notes_file.write_text(notes, encoding="utf-8")
        print(publish(ref, commit, notes_file, files))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
