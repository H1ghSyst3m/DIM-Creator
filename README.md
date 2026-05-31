# DIM-Creator

*A Windows desktop app for building DAZ Install Manager (DIM) packages.*

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![PySide6](https://img.shields.io/badge/GUI-PySide6-brightgreen)
![OS](https://img.shields.io/badge/OS-Windows-lightgrey)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-orange.svg)](LICENSE)
[![Total Downloads](https://img.shields.io/github/downloads/H1ghSyst3m/DIM-Creator/total)](https://github.com/H1ghSyst3m/DIM-Creator/releases)
[![Latest Release Downloads](https://img.shields.io/github/downloads/H1ghSyst3m/DIM-Creator/latest/total)](https://github.com/H1ghSyst3m/DIM-Creator/releases/latest)

DIM-Creator prepares DAZ Studio content for DAZ Install Manager. It stages your content, creates the required DIM metadata files, adds a cover image, and writes the final DIM-ready `.zip`.

## Features

- Build one DIM package or manage several builds in the same session.
- Reuse metadata from Build001, with per-build overrides when needed.
- Package all builds with **Package All** or only checked builds with **Package Selected**.
- Import `.zip`, `.rar`, and `.7z` archives through **Extract Archive**.
- Drag and drop files, folders, archives, and cover images.
- Save sessions, stores, tags, and DAZ folder settings between launches.
- Reorder builds by dragging them; part numbers update automatically.
- Keep build status visible so incomplete or empty builds are easy to spot.

## Requirements

- Windows
- Python 3.9+ when running from source
- 7-Zip or UnRAR in `PATH` for `.rar` and `.7z` extraction

Plain `.zip` archives work without an external extractor. For `.rar` and `.7z`, install [7-Zip](https://www.7-zip.org/) or UnRAR and make sure the executable is available from your terminal.

## Download

1. Download the latest release from [GitHub Releases](https://github.com/H1ghSyst3m/DIM-Creator/releases/latest).
2. Extract the release archive.
3. Run `DIM-Creator.exe`.

Windows SmartScreen may warn about an unknown publisher. If you trust the download source, choose **More info** and then **Run anyway**.

## Quick Start

1. Launch DIM-Creator. The first build is created at `Documents/DIMCreator/Builds/Build001/Content`.
2. Choose a store, enter the product name and SKU, and add a cover image if you have one.
3. Add DAZ content to the current build's `Content` folder by dragging files in or using **Extract Archive**.
4. Check the build status. A package needs content plus the required metadata fields.
5. Click **Package All** to build every package, or **Package Selected** to build only checked items.

The finished DIM package is a `.zip` containing the staged content, generated manifest files, support metadata, and cover image.

## Common Workflows

### Single Build

Use Build001 for one product or package part. Add the DAZ folders, fill in the product fields, and click **Package All**.

### Multiple Builds

Use **+ Add Build** to create Build002, Build003, and so on. New builds inherit shared fields from Build001, but you can override values such as SKU, product name, tags, or cover image on each build.

Use **Sync to All Builds** when you want Build001 to push shared metadata to the other builds. Use **Sync from Build 1** on a child build when you want to discard its overrides and return to the inherited values.

### Archive Imports

Use **Extract Archive** for `.zip`, `.rar`, or `.7z` files. The extraction dialog lets you separate template archives, content archives, and ignored files, then assign content archives to the correct build.

## Data Locations

- **Build workspaces:** `Documents/DIMCreator/Builds/Build001/Content`, `Build002/Content`, etc.
- **Session file:** `Documents/DIMCreator/Sessions/session.json`
- **Session backups:** `Documents/DIMCreator/Sessions/backups`
- **Logs:** `Documents/DIMCreator/Logs`
- **Config files:** `Documents/DIMCreator/Config`

Custom stores, tags, folder settings, and saved sessions are kept outside the application folder so they survive app updates.

## Run from Source

```powershell
git clone https://github.com/H1ghSyst3m/DIM-Creator.git
cd DIM-Creator
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

To build the Windows executable locally:

```powershell
pip install -r requirements-build.txt
pyinstaller -y DIM-Creator.spec
```

## Keyboard Shortcuts

### Main Window

- `Ctrl+G` - Generate GUID
- `Ctrl+Enter` - Generate a DIM package for the current build
- `Ctrl+N` - Clear fields and clean the current build folder

### File Explorer

- `Ctrl+E` - Open the current folder in Windows Explorer
- `Delete` - Delete the selected file or folder
- `Ctrl+C` / `Ctrl+X` / `Ctrl+V` - Copy, cut, or paste files
- `F2` - Rename the selected item
- `F5` - Refresh the file tree

## Troubleshooting

- **`.rar` or `.7z` archives do not extract:** Install 7-Zip or UnRAR and add it to `PATH`.
- **The app reports no DAZ folders:** The content should include folders such as `data`, `People`, or `Runtime`.
- **SmartScreen blocks the executable:** Use **More info** and **Run anyway** if you trust the release download.
- **A build is incomplete:** Check that it has content, store, product name, SKU, and a valid GUID.

## Screenshot

<p align="center">
  <img width="781" height="721" alt="DIM-Creator main window" src="https://github.com/user-attachments/assets/f6744257-5d39-429b-acfc-c01c8ee74186" />
</p>

## Contributing

Bug reports and focused pull requests are welcome. Please use the issue tracker for bugs, questions, and feature ideas.

## License

DIM-Creator is licensed under the GNU GPL v3. See [LICENSE](LICENSE).

<sub>"DAZ" and "DAZ Install Manager" are trademarks of their respective owners. This project is not affiliated with or endorsed by DAZ 3D.</sub>
