# DIM-Creator

*A fast PySide6 app for creating, packaging, and managing DAZ Install Manager (DIM) packages.*

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![PyQt5](https://img.shields.io/badge/GUI-PySide6-brightgreen)
![OS](https://img.shields.io/badge/OS-Windows-lightgrey)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-orange.svg)](LICENSE)
[![Total Downloads](https://img.shields.io/github/downloads/H1ghSyst3m/DIM-Creator/total)](https://github.com/H1ghSyst3m/DIM-Creator/releases)
[![Latest Release Downloads](https://img.shields.io/github/downloads/H1ghSyst3m/DIM-Creator/latest/total)](https://github.com/H1ghSyst3m/DIM-Creator/releases/latest)

**DIM-Creator** stages DAZ Studio content, generates the required DIM XML files, adds a cover image, and bundles everything into a ready-to-install DIM `.zip`—without the tedious manual setup.

---

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [System Requirements](#system-requirements)
- [Download & Install (EXE)](#download--install-exe)
- [Run from Source](#run-from-source)
- [Quick Start](#quick-start)
- [How Packaging Works](#how-packaging-works)
- [Workflows](#workflows)
- [Configuration & Data Paths](#configuration--data-paths)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Troubleshooting](#troubleshooting)
- [Screenshots](#screenshots)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

### What is it?
A desktop app (PySide6 + qfluentwidgets) that builds **DAZ Install Manager (DIM)** packages.

### Who is it for?
DAZ Studio users, creators, and vendors who want fast, repeatable, and tidy DIM packages—with correct folder layout, a cover image, and the required XML manifests.

---

## Features

- **Multi-Build Workflow** — Manage unlimited builds (Build 01, 02, 03...) in one session with automatic field inheritance
- **Batch Operations** — Package multiple builds at once with progress tracking and summary reports
- **Smart Extraction** — Three-column dialog intelligently sorts templates, content, and ignored items
- **Session Persistence** — All builds auto-save to disk and restore on next launch
- **Drag-Drop Reordering** — Rearrange builds by dragging them, with automatic part number updates
- **Field Synchronization** — Build 1 acts as parent; children inherit metadata with selective overrides
- **Build Status Indicators** — Visual feedback showing which builds are ready (✅), incomplete (⚠️), or empty (📭)
- **Quick Actions** — Per-build delete buttons, context menus, and keyboard shortcuts
- **Make DIM packages in seconds** — just point to your files or an archive and click Package All
- **Drag & drop file management** — organize your content without leaving the app
- **Automatic folder detection** — files are placed where DIM expects them
- **Cover art made easy** — drop an image and it's formatted for DIM automatically
- **Warnings before mistakes** — get notified about layout problems before packaging
- **Store & tag presets** — save time with one-click product metadata
- **Keeps your presets across updates** — your custom stores and tags won't vanish after upgrading
- **Works without Python** — available as a ready-to-run Windows `.exe`

---

## System Requirements

- **OS:** Windows (officially supported)
- **Python:** 3.9+ (only needed when running from source)
- **External extractors** (for `.rar` / `.7z`):
  - **7-Zip** or **UnRAR** must be installed and available in your system `PATH`

> Tip: Installing [7-Zip](https://www.7-zip.org/) and enabling “Add to PATH” makes `.7z`/`.rar` imports work out of the box.

---

## Download & Install (EXE)

1. Download the latest release from **GitHub Releases**.
2. Unzip and run `DIMCreator.exe` — no Python environment required.

If SmartScreen warns about an unknown publisher, choose **More info → Run anyway**.

---

## Run from Source

```bash
git clone https://github.com/H1ghSyst3m/DIM-Creator.git
cd DIM-Creator
python -m venv .venv
. .venv/Scripts/activate  # Windows
pip install -r requirements.txt
python app.py
```

---

## Quick Start

1. Launch the app — your workspace is organized under `Documents/DIMCreator/Builds/`.
2. Build001 is created automatically with its own `Content` folder.
3. Pick your store, fill in product name/SKU, and (optional) add a cover image.
4. Add content by dragging it in or importing an archive to the current build's Content folder.
5. Click **Package All** to create your DIM-ready `.zip`.
6. Use **+ Add Build** to create additional builds (Build 02, 03, etc.) that inherit metadata from Build 01.
7. Rearrange builds by dragging them in the list, or use the trash icon for quick deletion.
8. Click **Package All** to process all builds at once or select specific builds and click **Package Selected** to process only those.

---

## How Packaging Works

- Your content folder becomes the installable DIM package.
- The app adds DIM’s required metadata files.
- A properly sized cover image is included.
- The result is a single, ready-to-install `.zip`.

---

## Workflows

### Single-Build Workflow (Traditional)
1. Work in Build001 folder shown in File Explorer panel.
2. Fill in all metadata fields (store, product name, SKU, tags, image).
3. Add your DAZ content to the Content folder (drag files or extract archives).
4. Click **Package All** when ready.

### Multi-Build Workflow (Batch Processing)
1. Start with Build001 — fill in all metadata that will be shared across builds.
2. Click **+ Add Build** to create Build002 — it automatically inherits metadata from Build 001.
3. Add content to Build002's folder (File Explorer switches automatically when you select different builds).
4. Override specific fields in Build002 if needed (e.g., different SKU, image, or Product Name).
5. Create more builds (Build003, Build004...) as needed — each inherits from Build001 by default.
6. Check the boxes next to builds you want to package.
7. Click **Package All** to process all checked builds with progress tracking.
8. Review the summary dialog showing which packages succeeded or failed.

### Smart Extraction for Multi-Build Setup
1. Collect all your archives (.zip, .rar, .7z) in one location.
2. Click **Extract Archive** and select multiple files.
3. The extraction dialog shows three columns:
   - **Template**: Archives detected as templates (based on settings)
   - **Extract**: Content archives you want to process
   - **Ignored**: Archives you don't need
4. Move items between columns by dragging or using arrow buttons.
5. Assign each content archive to a build number (creates builds if needed).
6. Click Extract — all content goes to the correct build folders automatically.

### Synchronization Examples
**Updating All Builds from Build 1:**
1. Edit metadata in Build001 (e.g., change store or tags).
2. Click the **Sync to All Builds** dropdown.
3. Choose "Sync All Fields" or select specific fields (Store, SKU, Tags, etc.).
4. All child builds update instantly.

**Pulling Updates to a Specific Build:**
1. Select Build002 (or any child build).
2. Click **Sync from Build 1** button.
3. All field overrides are cleared and Build002 inherits fresh values from Build001.

### Reordering Builds
1. Drag Build003 above Build002 in the list.
2. Part numbers automatically renumber (old Build003 becomes new Build002).
3. If you move a build to position 1, it becomes the new parent and transfers its metadata.

### From a folder
1. Put your DAZ content into the current build's `Content` folder (shown in File Explorer).
2. Fill in details → Package All.

### From an archive
1. Import `.zip`, `.rar`, or `.7z` into the current build.
2. The app extracts only the correct DAZ folders.
3. Fill in details → Package All.

---

## Configuration & Data Paths

- **Build Workspaces:** `Documents/DIMCreator/Builds/Build001/Content`, `Build002/Content`, etc.
- **Session Data:** `Documents/DIMCreator/Sessions/session.json` (auto-saved with 5 backups)
- **Logs:** `Documents/DIMCreator/Logs`
- **Config Files:** `Documents/DIMCreator/Config/` (stores, tags, DAZ folder list)

Your custom settings, presets, and build sessions are preserved after updates.

---

## Keyboard Shortcuts

### Main window
- `Ctrl+G` — Generate GUID
- `Ctrl+Enter` — Generate DIM package for current build
- `Ctrl+N` — Clear fields and clean current build folder (deletes Manifest, Supplement, and all content)

### File Explorer
- `Ctrl+E` — Open current folder in Windows Explorer
- `Delete` — Delete selected file or folder
- `Ctrl+C` / `Ctrl+X` / `Ctrl+V` — Copy / Cut / Paste files
- `F2` — Rename selected item
- `F5` — Refresh file tree

---

## Troubleshooting

- **“.rar/.7z not extracting”** → Install **7-Zip** or **UnRAR** and add to `PATH`.
- **No DAZ folders found** → Content should start with folders like `data`, `People`, `Runtime`.
- **SmartScreen warning** → Allow the app via “More info → Run anyway”.

---

## Screenshots

<p align="center">
  <img width="781" height="721" alt="DIM-Creator main window" src="https://github.com/user-attachments/assets/efc12e42-251d-441b-a236-d99befa5759b" />
</p>

---

## Contributing

Contributions are welcome!  
Open issues for bugs or ideas. PRs should use feature branches and focused commits.

---

## License

GNU GPL v3 — see [LICENSE](LICENSE).  
<sub>“DAZ” and “DAZ Install Manager” are trademarks of their respective owners. This project is not affiliated with or endorsed by DAZ 3D.</sub>
