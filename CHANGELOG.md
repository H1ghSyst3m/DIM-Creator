# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---
## [Unreleased]
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