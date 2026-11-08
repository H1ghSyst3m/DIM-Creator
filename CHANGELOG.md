# Changelog

[Release notes for the Python version](legacy/CHANGELOG.md)

## [3.0.0]

DIM Creator 3.0 is a complete rewrite of the Python/PySide6 app. The new workspace takes you from a folder of DAZ files or a set of downloads to finished packages for DAZ Install Manager.

### Highlights

- **Rebuilt in Rust.** The app feels more responsive, even while it copies files, checks packages or builds ZIPs in the background. The new desktop interface is built with Tauri and React.
- **A worklist for multi-part products.** Group related packages and drag them into order. Choose which product details to copy across the group and, if you want, renumber the parts to match.
- **Import straight from your downloads.** Drop ZIP, RAR or 7z archives into the worklist. DIM Creator opens them directly and finds the DAZ content, including in supported nested archives. Send several downloads to one package, split them across packages, or save an archive as a template.
- **Build a whole product in one go.** Export a group, a selection or the entire worklist as DIM ZIPs. Review where each ZIP will go and approve any replacements before the build starts.
- **Eight themes with live previews.** Choose from six Classic themes and two Glass themes, Frost and Nocturne. Try a theme across the whole app before saving it.

### Added

- Collapse groups to make room, drag an entire group to move it, or undo the last change to the grouping or order.
- In **Synchronize…**, choose a source package and the fields to copy, including store and prefix, SKU, product name, tags, cover and support cleanup. Preview the new values and ZIP names before applying changes.
- Reorder and renumber parts on their own or while synchronizing product details.
- Drop archives onto the import area in **Files** to use the open package as the destination. Create a group from the imported packages or extend an existing group during import.
- Use **Save as template** to copy a complete archive, including one nested inside another download, to your chosen template folder. Detected templates can be preselected for review.
- Search the worklist by product name, SKU or part number.
- Choose a default store and tags for new packages. Edit the store and prefix list, available tags, and the folders DIM Creator recognizes as DAZ content.
- Change the theme directly from the header.
- Use the header pin to keep DIM Creator above other windows for the current session.

### Changed

- Each package is now an independent draft with its own content, cover and metadata. Edits to a package in a group apply to that package alone.
- Group synchronization checks product details, covers and ZIP destinations directly, even when the packages contain thousands of files.
- Drafts and worklist changes are saved as you work. Package order, group membership and collapsed groups are restored when you reopen the app.
- The importer uses your DAZ folder list to find the content root and strips outer wrapper folders.
- Import conflict checks use a file path index. Changing where a download goes reuses the files DIM Creator already unpacked.
- DIM Creator streams large files during import and export to keep memory use down.
- Import review shows what will be imported, saved as a template or excluded. Choose whether to replace or skip conflicting files, then see which packages and templates were saved.
- The file browser supports multi-select, copy, cut and paste, renaming, folder creation and drag and drop within a package. You can exclude individual files or folders from export while keeping them in the draft.
- Add a JPEG, PNG or WebP cover from disk or by dropping it onto the cover area. DIM Creator prepares the JPEG cover for the package's `Runtime/Support` folder.
- Package validation points to the fields or files that need attention. The preview shows the proposed ZIP filename and the files included in the export.
- Build the open package on its own when you only need one ZIP. Save exports directly in the output folder or organize them in dated subfolders.
- Each ZIP contains `Manifest.dsx`, `Supplement.dsx` and the selected content. DIM Creator verifies the new archive before replacing an existing ZIP.
- The new interface is available in English and German. On narrower windows, switch between the worklist, properties and files using tabs.

### Upgrading from the Python version

- The Windows x64 installer uses NSIS and requires Microsoft Edge WebView2 Runtime.
- Drafts, covers, the worklist and settings are stored in `%LOCALAPPDATA%\com.dimcreator`. Exported ZIPs go to the output folder you choose.
- To bring a draft over from the Python app, add its original content to a new package and enter the product details.
