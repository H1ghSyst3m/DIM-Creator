# Changelog

## Unreleased

## v2.1.0

### Added
- Added conflict handling for archive imports. Existing files can now be replaced, skipped, or the whole import can be cancelled.
- Added automatic recovery for damaged session and configuration files using the latest valid backup.
- Added persistent cover storage under `Documents/DIMCreator/Assets/Covers`.
- Added support for the `3dsMax` product tag.
- Added SBOM and SHA-256 checksum files to GitHub releases.

### Changed
- Reworked DIM package creation to use a temporary staging folder. Building a package no longer changes the original content folder.
- Packages are now checked before they replace an existing ZIP. This includes the ZIP itself, its XML files, manifest entries, and filenames.
- Package creation now always starts with a fresh ZIP, preventing old files from remaining in rebuilt packages.
- Improved DIM filename validation for prefixes, SKUs, package parts, and product names.
- `Ctrl+Enter`, `Ctrl+Return`, **Package All**, and **Package Selected** now use the same packaging process.
- Reworked archive imports so files are checked before anything is extracted or copied.
- ZIP files are now handled directly by the app. 7z and RAR files use controlled 7-Zip or UnRAR processes instead of `patool`.
- Archive imports now have sensible limits for file count, unpacked size, nesting, compression ratio, and available disk space.
- Archives found inside a DAZ content folder are kept as product files instead of being treated as another package.
- Template detection now only matches the words `template` and `templates`, so `Temple*.zip` files remain product content.
- Multipart archives are now checked for missing or duplicate parts.
- Session data now uses build IDs instead of list positions, making build selection more reliable after deleting or reordering builds.
- Sessions and configuration files are now saved atomically and keep up to ten backups.
- The file explorer is now limited to the current build’s `Content` folder.
- File and folder names are checked against Windows naming rules before they are created or renamed.
- Deleted files are sent to the Windows Recycle Bin when possible. Permanent deletion requires another confirmation.
- Cover images are saved as normalized JPEG files. Downloads are limited to 20 MiB, 40 megapixels, and 15 seconds.
- Updated configuration defaults from `IM` to `LOCAL` for local products and corrected `3DX` to `D3X`.
- Renamed the `LightWave` tag to `Lightwave`.
- `Plugin` and `Software` tags are no longer accepted because the app currently creates content packages only.
- Updated source requirements to Windows 10/11 x64 and Python 3.11–3.14.
- Updated and pinned PySide6, PySide6-Fluent-Widgets, Pillow, and PyInstaller.
- Removed `patool`, the optional QFluentWidgets `full` dependencies, broad PyInstaller module collection, and UPX compression.
- Updated the GitHub Actions workflow to run tests before building and to use restricted permissions.

### Fixed
- Fixed empty sessions reusing the previous package GUID after restarting DIM-Creator.
- Fixed incomplete GUID edits triggering session save errors before the entry was finished.
- Fixed existing packages being overwritten when packaging failed or was cancelled.
- Fixed invalid session values being used in package filenames and output paths.
- Fixed case-insensitive filename collisions producing broken packages.
- Fixed package destinations inside the managed build folder being accepted.
- Fixed internal resource archives being extracted as wrapper packages.
- Fixed failed archive imports leaving partially copied files or newly created builds behind.
- Fixed extraction workers changing session data directly from background threads.
- Fixed the application closing while packaging or extraction threads were still running.
- Fixed **Save & Exit** closing the app when the session could not be saved.
- Fixed damaged or newer session files being silently overwritten.
- Fixed empty DAZ content folders being treated as valid product content.
- Fixed removing a cover from Build 1 not clearing the saved cover value.
- Fixed old cover downloads completing after a newer download was started.
- Fixed closing the app changing the selected cover.
- Fixed failed file replacements damaging the original destination file.

## v2.0.2

### Fixed
- DIM package ZIP names now use DIM-compatible product name segments so archives with spaces, underscores, hyphens, or dots in the product name are recognized correctly.
- Runtime/Support cover image names now normalize dots the same way DIM support metadata does, preventing missing Smart Content covers for products such as `G8.1`.

## v2.0.1

### Fixed
- File Explorer now refreshes properly after clearing a build or starting a new session.
- Fixed "Clean Support Directory" checkbox being ignored during batch packaging operations ("Package All" and "Package Selected").

## v2.0.0

### What's New in v2.0

**Multi-Build Workspace**
Work on several builds at once instead of one at a time. Create Build 01, 02, 03... and manage them together in a single session.

**Batch Operations**
- Package multiple builds together
- Track progress for each build and overall completion
- Get a summary report showing successes and failures
- Check/uncheck builds to control which ones get processed

**Archive Extraction Upgrades**
- Process multiple archive files in one operation
- Sort archives into three categories: templates, content to extract, and files to skip
- Let the app detect templates automatically based on your preferences
- Assign archives to specific build numbers

**Workspace Changes**
Your files now live in `Builds/Build001/Content/`, `Build002/Content/`, etc. instead of a single `DIMBuild/` folder.

**Field Inheritance**
Build 01 is the source for common fields. Build 02 and higher copy these values automatically. You can override any field in child builds when needed.

**Synchronization Tools**
- Push updates from Build 01 to all other builds
- Pull latest values from Build 01 into a specific build
- Sync individual fields (store, SKU, tags) or everything at once

**Session Saves**
All your builds save to `session.json` automatically. The app keeps 5 backup copies. When you reopen the app, everything comes back.

**New UI Elements**
- Visual status badges: ✅ ready, ⚠️ needs info, 📭 empty
- Delete button on each build row
- Drag builds to reorder them
- "New Session" button to start over

### Important Changes

**Settings**: "Copy Template Archives" renamed to "Enable Template Detection" (clearer meaning)

## v1.2.1
### Changed
- Updated dependencies in `requirements.txt` and `requirements-build.txt` to separate build-time and runtime packages.
  - Moved `pyinstaller` to `requirements-build.txt`.
  - Removed `nuitka` from `requirements.txt`.
- **Code Quality**: Resolved 130+ PEP 8 violations (whitespace, formatting, imports, line length). All files now pass flake8 linting.
- **File Explorer Drag & Drop**: Hardened DIMBuild boundary check. Now uses `os.path.commonpath` instead of string-based `startswith` to correctly detect drops outside of the DIMBuild directory (avoids false positives like “…/DIMBuild-Backup”).
- **Code Refactoring**: Decoupled packaging from the GUI; introduced packaging_utils.py and a PackagingWorker so the entire packaging pipeline runs off the UI thread.
- **Code Refactoring**: Decoupled content extraction from the GUI; introduced extraction_utils.py and a ContentExtractionWorker so the entire extraction process runs off the UI thread.
- Changed compression level for packaging from 9 (maximum) to 6 (default) to improve speed, since maximum compression yields minimal size savings for typical DIM content.
- **Packaging Progress Reporting**: Progress for zipping is now calculated based on file size instead of file count. This provides a much more accurate and smooth progress bar, especially for packages containing large files.
- **Performance**: The application will now use 7-Zip for packaging if it is installed and available in the system's PATH. This significantly improves compression speed. If 7-Zip is not found, it gracefully falls back to the built-in `zipfile` module.

### Fixed
- **Session Persistence**: Store Field dropdown selection and Auto Prefix checkbox state are now saved and restored between sessions.
- Fixed issue at clearing function where store field was reset to default instead of keeping user selection.
- **Extraction Concurrency (Drag & Drop)**: Prevent starting a second extraction while one is already running.
- Fixed issue where last used destination folder was not saved after extraction until closing the app.

## v1.2.0
### Added
- **Progress Ring Overlay**: Centered progress ring with percentage display shown over the preview image during packaging.
- **Preview Filename Improvements**:
  - Monospace font for improved readability.
  - Dedicated copy-to-clipboard button with confirmation toast.
  - Adjusted position for clearer separation from action controls.

### Changed
- **Complete UI Redesign** for a cleaner, more modern Fluent-inspired look.
- Migrated project from **PyQt5** → **PySide6** for long-term Qt6 compatibility.
  - Replaced all `pyqtSignal` usages with `Signal`.
  - Updated imports to PySide6 modules (`QtWidgets`, `QtCore`, `QtGui`, `QtNetwork`).
  - Updated enums to Qt6 namespaced versions (`Qt.WindowType.*`, `Qt.WidgetAttribute.*`, `QAbstractItemView.SelectionBehavior.*`, `QAbstractItemView.EditTrigger.*`).
- Switched to **QFluentWidgets (Community Edition [full])** for modern Fluent UI components.
- Standardized logging across modules:
  - All modules now use a named logger via `get_logger`.
  - Unified formatting and log levels.
- Safer tooltip handling:
  - Added validity checks (`shiboken6.isValid`) before closing tooltips.
  - Automatic tooltip cleanup now prevents crashes from double-closing deleted widgets.
- Cleaned up duplicate and unused imports across modules.

### Fixed
- Extraction no longer crashes the app when multiple archives are found (worker now exits gracefully).
- Fixed crash caused by destroying QThreads while still running (moved cleanup to `finished`).
- Fixed crashes caused by legacy PyQt5-specific classes.
- Fixed double “process completed” notifications by separating success and error signals in zipping.
- Fixed tooltip crash (`Internal C++ object already deleted`) by validating widget existence before closing.
- Drop events outside the build directory are now explicitly ignored to prevent inconsistent state.
- Fixed cases where certain dialogs or toasts did not display correctly.

### ⚠️ Important
If you run DIM-Creator from source:  
Please **reinstall all pip dependencies** (`pip install -r requirements.txt`) because of the migration from **PyQt5** to **PySide6** and the switch to **PySide6-QFluentWidgets**.

## v1.1.2
### Added
- Live ZIP filename preview (bottom-right footer). Updates in real time as you change Store/Auto Prefix, Prefix, SKU, Part, or Product Name.

### Changed
- Manifest generation now sorts directories and files to ensure deterministic output.  
- Zipping progress reporting is more accurate and stable:
  - Guards against divide-by-zero when no files are present.
  - Caps percentage updates correctly to avoid misleading 100% before completion.  
- Common OS cruft files (e.g., `.DS_Store`, `Thumbs.db`, `desktop.ini`, `__MACOSX`) are now ignored during zipping and content extraction to prevent clutter.  
- Removed unused helper `sanitize_product_name` to reduce maintenance surface.

### Fixed
- Support directory cleanup is now more robust:
  - Handles read-only files by forcing writable permissions before deletion.
  - Uses a safe fallback for stubborn files and folders to avoid cleanup failures.
- GUID input validation now correctly requires a full UUID (anchored regex), preventing partial matches.
- Product part input now correctly formats numbers with leading zeros.
- Github link correction for the license file.

## v1.1.1 - 2025-08-13
### Added
- Drag and drop support for images.
  - You can now drag local image files or URLs from browsers directly into the app.

### Changed
- Replaced logo with a new design.
  - Removed old logo assets from the repository.
  - Added `favicon.ico` with a universal size.
- Improved overall UI consistency and responsiveness.

### Fixed
- Improved extraction error handling to properly close tooltips.
- Fixed potential crash when accessing tooltip attributes.
- Fixed issue where common DAZ folders were not scanned case-insensitively.
- Fixed issue where temporary image files were not deleted on application exit.
- Fixed potential crash when accessing image attributes.
- Fixed issue where image attributes were not properly reset on removal.

## v1.1.0 - 2025-08-12
### Added
- Support for update checks.
  - Automatic update manager that checks for updates in the background and notifies the user.
  - Support for manual update checks.

### Changed
- Improved version parsing and comparison to normalize tags and handle semantic versions accurately.
- Configuration update logic now preserves user-modified entries and only appends missing defaults.
  - Store entries are matched case-insensitively to avoid duplicates (e.g., `RenderHub` vs `renderhub`).
  - User-defined field values are never overwritten during upgrades; only missing fields from defaults are added.
  - Order of existing configuration entries is preserved.
- Editor save routines now correctly store the current configuration version to prevent unnecessary repeated upgrades.

### Fixed
- Prevented loss of custom store prefixes or tags when upgrading configuration files.
- Fixed potential crash when configuration `data` field was malformed or of the wrong type.
- Fixed issue where certain list-type configurations would lose their original ordering after upgrade.

## v1.0.0 - 2025-08-12
### Added
- Initial public release of **DIM-Creator**.
- Windows build pipeline using PyInstaller.
- Automated GitHub Actions workflow for:
  - Building EXE files on Windows.
  - Packaging versioned release ZIP with README and LICENSE included.
  - Uploading artifacts for CI runs.
  - Attaching raw EXE and ZIP files to GitHub Releases for tagged versions.
- Bundled all necessary assets into the executable for easy distribution.

### Changed
- Optimized startup time by preloading UI components.
- Improved asset packaging to ensure all files are included in the release.
