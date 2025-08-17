# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## v1.2.0 - 2025-08-17
### Changed
- Code cleanup and maintenance improvements:
  - Removed redundant comments from DIMPackageGUI initialization to improve code readability.
  - Enhanced code organization and maintainability.

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