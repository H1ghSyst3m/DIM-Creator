# DIM Creator

DIM Creator turns DAZ Studio content on your PC into ZIP packages for DAZ Install Manager (DIM). Use it to package a local folder, bring in content from an archive, or prepare several products at once.

The 3.0 rewrite replaces the Python/PySide6 app with Rust, Tauri and React/TypeScript. The old source, build instructions and changelog are in the [legacy archive](legacy/README.md).

## Download

On Windows x64, download `DIM-Creator_<version>_x64-setup.exe` and run it. The app needs Microsoft Edge WebView2 Runtime. Compare the installer's SHA-256 hash with `SHA256SUMS.txt` from the same release to check the download. [GitHub Releases](https://github.com/H1ghSyst3m/DIM-Creator/releases)

For a fresh per-user installation, Setup uses `%LOCALAPPDATA%\DIM Creator` by default.

If the setup file isn't listed yet, follow the source build instructions below.

## Build your first DIM package

1. Click **+ Product** in the worklist. Enter the product name and an SKU of 1–8 digits greater than zero. Choose the store and add a cover image if you want one.
2. In **Files**, add the content with **Add files**, **Add folders**, or drag and drop. Put folders such as `data`, `People` and `Runtime` directly under the package's `Content` folder, as they would appear at the top of your DAZ Studio library.
3. Choose an **Output folder** at the bottom. Click **Build this package**, or tick several packages and use **Build selection**. Review the export and confirm. With **By date** selected, look for the ZIP in the dated subfolder of your output folder.
4. Copy the finished ZIP into the **Package Archive** folder configured in DAZ Install Manager. Refresh DIM and look under **Ready to Install**. Check the destination library before installing. [DAZ Install Manager guide](https://docs.daz3d.com/public/software/install_manager/userguide/use_install_manager/tutorials/installing_with_install_manager/start)

The ZIP contains `Manifest.dsx`, `Supplement.dsx` and the selected files under `Content/`. The filename is based on the package prefix, SKU, part number and product name.

If an export is blocked, open **Package validation** from the package status. It shows which fields or files need attention. For a content warning, check the folders under `Content` and the **Settings → DAZ folders** list.

## Import archives and templates

Use **Import** in the worklist to bring in ZIP, RAR or 7z archives, including archives inside archives. Use **Extract archive…** in **Files** to start with the current package as the destination. The import dialog shows what it found and lets you choose a new or existing package. Check the assignments and file conflicts before you start the import.

To keep an archive for later, set **Settings → General → Template folder**, then choose **Save as template** for that archive in the import dialog. DIM Creator copies the archive to the template folder when you confirm the import.

## Working with several packages

Tick related packages and click **Group** to keep them together in the worklist. Use **Synchronize…** in the group's menu to choose a source package, the metadata to copy, and whether to renumber parts. Preview the changes before applying them. You can still edit or export any package on its own.

Set your usual store and tags in **Settings** for new packages. Use the theme menu to choose a Classic or Glass theme, or click the pin to keep DIM Creator above other windows while moving files around.

## Where your work is saved

Open `%LOCALAPPDATA%\com.dimcreator` in File Explorer to find your package drafts, copied content, covers, worklist and settings. Close DIM Creator before backing up that folder. Exported ZIPs go to the output folder you chose.

The Python app uses `Documents\DIMCreator`.

## Build from source

You need Node.js 24, Rust stable with the Windows MSVC toolchain, and Visual Studio C++ Build Tools with the Windows SDK. Install the Rust `rustfmt` and `clippy` components to run the CI checks.

From the repository root, install the dependencies and start the app.

```powershell
npm ci
npm run tauri -- dev
```

To build the installer, run `npm run tauri -- build --ci -- --locked`. The setup file is in `target/release/bundle/nsis/`.

## Project layout

| Directory | What it contains |
|---|---|
| `core/` | Rust workspace storage, archive import, validation and DIM package generation |
| `src-tauri/` | Native Windows integration, commands exposed to the UI and NSIS configuration |
| `src/` | React UI, translations, themes and frontend tests |

Run `npm test` for the frontend and `cargo test --workspace --locked` for Rust. `src/bindings.ts` holds the types shared with the Rust commands. Regenerate it with `npm run bindings:generate` after changing a Rust request or response type. `npm run bindings:check` verifies the committed file.

For a release, run `npm run version:bump -- 3.0.1` with Python 3.11 or newer available as `python`. Add the matching release notes to the [changelog](CHANGELOG.md), then commit, tag and push separately. See `npm run version:bump -- --help` for details. The release workflow checks the tag, project versions and changelog before building.

[Changelog](CHANGELOG.md) · [AGPL-3.0-only license](LICENSE)
