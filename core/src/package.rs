use crate::error::{AppError, Severity};
use crate::filesystem::{
    case_key, check_chain, hash_file_identity, is_link, open_regular_file, open_replaceable_file,
    path_contains, validate_segment,
};
use crate::model::{Package, valid_prefix, valid_tag};
use image::{ImageFormat, ImageReader, Limits};
use quick_xml::{
    Writer,
    events::{BytesDecl, BytesEnd, BytesStart, Event},
};
use serde::Serialize;
use std::{
    collections::{BTreeMap, hash_map::DefaultHasher},
    fs::{self, File, Metadata},
    hash::{Hash, Hasher},
    io::{self, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};
use tempfile::NamedTempFile;
use uuid::Uuid;
use zip::{CompressionMethod, ZipArchive, ZipWriter, write::SimpleFileOptions};

pub(crate) const DAZ_PREFIXES: &[&str] = &["IM", "DZ", "DAZ", "DAZ3D", "TAFI"];
const MANIFEST: &str = "Manifest.dsx";
const SUPPLEMENT: &str = "Supplement.dsx";
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ts_rs::TS)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Scanning,
    Writing,
    Verifying,
    Publishing,
    Starting,
    Validating,
    Finished,
}

const BUFFER_SIZE: usize = 64 * 1024;

pub use crate::error::Issue;

#[derive(Debug, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct Preview {
    pub filename: Option<String>,
    pub entries: Vec<String>,
    pub issues: Vec<Issue>,
    pub size: u64,
}

struct Member {
    name: String,
    source: PathBuf,
    metadata: Metadata,
}

#[derive(Default)]
struct Inventory {
    members: Vec<Member>,
    names: BTreeMap<String, (String, bool)>,
    support_excluded: bool,
}

struct PackagePlan {
    preview: Preview,
    members: Vec<Member>,
    guid: Option<Uuid>,
    store_index: Option<String>,
}

pub fn preview(package: &Package, root: &Path, daz_folders: &[String]) -> Preview {
    plan(package, root, daz_folders, &AtomicBool::new(false)).preview
}

pub fn preview_cancellable(
    package: &Package,
    root: &Path,
    daz_folders: &[String],
    cancelled: &AtomicBool,
) -> Preview {
    plan(package, root, daz_folders, cancelled).preview
}

/// Export metadata only; deliberately does not inspect package contents or covers.
pub fn metadata_preview(package: &Package) -> Preview {
    let mut issues = Vec::new();
    let name = package.name.trim();
    let prefix = package.prefix.trim();
    let sku = number(package.sku.trim(), 8);
    let part = number(package.part.trim(), 2);
    let guid = Uuid::parse_str(package.guid.trim())
        .ok()
        .filter(|_| package.guid.trim().len() == 36);
    for (field, value) in [("name", name), ("store", package.store.trim())] {
        if value.is_empty() {
            issues.push(Issue::error("required", field));
        } else if !valid_xml_text(value) {
            issues.push(Issue::error("invalidText", field));
        }
    }
    if !valid_prefix(prefix) {
        issues.push(Issue::error("invalidPrefix", "prefix"));
    }
    if sku.is_none() {
        issues.push(Issue::error("invalidSku", "sku"));
    }
    if part.is_none() {
        issues.push(Issue::error("invalidPart", "part"));
    }
    if guid.is_none() {
        issues.push(Issue::error("invalidGuid", "guid"));
    }
    if package
        .tags
        .iter()
        .any(|tag| !valid_tag(tag) || !valid_xml_text(tag))
    {
        issues.push(Issue::error("invalidTags", "tags"));
    }

    let filename = sku.zip(part).and_then(|(sku, part)| {
        (!name.is_empty() && valid_xml_text(name) && valid_prefix(prefix))
            .then(|| zip_filename(prefix, sku, part, name))
    });
    Preview {
        filename,
        entries: Vec::new(),
        issues,
        size: 0,
    }
}

fn plan(
    package: &Package,
    root: &Path,
    daz_folders: &[String],
    cancelled: &AtomicBool,
) -> PackagePlan {
    let Preview {
        filename,
        mut issues,
        ..
    } = metadata_preview(package);
    let sku = number(package.sku.trim(), 8);
    let part = number(package.part.trim(), 2);
    let prefix = package.prefix.trim();
    let guid = Uuid::parse_str(package.guid.trim())
        .ok()
        .filter(|_| package.guid.trim().len() == 36);
    let store_index = sku.zip(part).and_then(|(sku, part)| {
        DAZ_PREFIXES
            .contains(&prefix)
            .then(|| format!("{sku}-{part}"))
    });
    let content = root.join("Content");
    let mut inventory = Inventory::default();
    let excluded: Vec<_> = package
        .excluded
        .iter()
        .map(|entry| case_key(entry))
        .collect();
    let result = check_chain(&content).and_then(|()| {
        scan(
            &content,
            "",
            package.clean_support,
            &excluded,
            &mut inventory,
            cancelled,
        )
    });
    match result {
        Err(error) => issues.push(Issue::cause("unsafeContent", "content", error)),
        Ok(()) => {
            if !inventory.members.iter().any(|member| {
                let mut parts = member.name.split('/').skip(1);
                parts.next().is_some_and(|root| {
                    parts.next().is_some()
                        && daz_folders
                            .iter()
                            .any(|folder| case_key(folder) == case_key(root))
                })
            }) {
                issues.push(Issue::error("noDazContent", "content"));
            }
            if let Some(member) = inventory.members.iter().find(|member| {
                let path = member.name.strip_prefix("Content/").unwrap_or(&member.name);
                daz_folders
                    .iter()
                    .any(|folder| case_key(folder) == case_key(path))
            }) {
                issues.push(Issue::value("unsafeContent", "content", &member.name));
            }
        }
    }
    if inventory.support_excluded {
        issues.push(Issue {
            error: AppError::new("supportExcluded"),
            severity: Severity::Warning,
            field: "support".into(),
        });
    }

    if let (Some(cover), Some(sku)) = (&package.cover, sku)
        && let Err(issue) = add_cover(&mut inventory, package, root, cover, sku)
    {
        issues.push(*issue);
    }
    inventory.members.sort_by(|a, b| a.name.cmp(&b.name));
    let entries = [MANIFEST.to_owned(), SUPPLEMENT.to_owned()]
        .into_iter()
        .chain(inventory.members.iter().map(|member| member.name.clone()))
        .collect();
    let size = inventory
        .members
        .iter()
        .map(|member| member.metadata.len())
        .sum();
    PackagePlan {
        preview: Preview {
            filename,
            entries,
            issues,
            size,
        },
        members: inventory.members,
        guid,
        store_index,
    }
}

fn number(value: &str, digits: usize) -> Option<u32> {
    if value.len() > digits || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    value.parse().ok().filter(|number| *number > 0)
}

fn valid_xml_text(value: &str) -> bool {
    value.chars().all(|character| matches!(character, '\u{20}'..='\u{d7ff}' | '\u{e000}'..='\u{fffd}' | '\u{10000}'..='\u{10ffff}'))
}

fn zip_filename(prefix: &str, sku: u32, part: u32, name: &str) -> String {
    let stem = format!("{prefix}{sku:08}-{part:02}_");
    let name: String = name
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .take(255 - stem.len() - ".zip".len())
        .collect();
    let name = if name.is_empty() { "Package" } else { &name };
    format!("{stem}{name}.zip")
}

fn scan(
    path: &Path,
    parent: &str,
    clean_support: bool,
    excluded: &[String],
    inventory: &mut Inventory,
    cancelled: &AtomicBool,
) -> Result<(), AppError> {
    check_chain(path)?;
    for entry in fs::read_dir(path).map_err(|error| path_error(path, error))? {
        check_cancelled(cancelled)?;
        let entry = entry.map_err(|error| path_error(path, error))?;
        let source = entry.path();
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| AppError::new("invalidFilename").param("path", source.display()))?;
        if matches!(
            name.to_ascii_lowercase().as_str(),
            ".ds_store" | "desktop.ini" | "thumbs.db"
        ) {
            continue;
        }
        let relative = if parent.is_empty() {
            name.clone()
        } else {
            format!("{parent}/{name}")
        };
        let folded = case_key(&relative);
        if clean_support && folded == "RUNTIME/SUPPORT" {
            inventory.support_excluded = true;
            continue;
        }
        if excluded.iter().any(|item| {
            folded == *item
                || folded
                    .strip_prefix(item)
                    .is_some_and(|rest| rest.starts_with('/'))
        }) {
            if folded == "RUNTIME/SUPPORT" || folded.starts_with("RUNTIME/SUPPORT/") {
                inventory.support_excluded = true;
            }
            continue;
        }
        let metadata = fs::symlink_metadata(&source).map_err(|error| path_error(&source, error))?;
        if is_link(&metadata) {
            continue;
        }
        validate_segment(&name)?;
        if !valid_xml_text(&name) {
            return Err(AppError::new("invalidPackagePath").param("path", source.display()));
        }
        if inventory
            .names
            .insert(folded, (relative.clone(), metadata.is_dir()))
            .is_some()
        {
            return Err(AppError::new("caseCollision").param("path", source.display()));
        }
        if metadata.is_dir() {
            scan(
                &source,
                &relative,
                clean_support,
                excluded,
                inventory,
                cancelled,
            )?;
        } else if metadata.is_file() {
            inventory.members.push(Member {
                name: format!("Content/{relative}"),
                source,
                metadata,
            });
        }
    }
    Ok(())
}

fn support_segment(value: &str) -> String {
    let mut result = String::new();
    let mut separated = false;
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
            if separated {
                result.push('_');
                separated = false;
            }
            result.push(character);
        } else {
            separated = true;
        }
    }
    result.trim_matches('_').to_owned()
}

pub fn managed_cover(root: &Path, cover: &str) -> Result<(Vec<u8>, Metadata), AppError> {
    if validate_segment(cover).is_err()
        || cover
            .strip_prefix("cover-")
            .and_then(|value| value.strip_suffix(".jpg"))
            .filter(|id| id.len() == 36)
            .and_then(|id| Uuid::parse_str(id).ok())
            .is_none()
    {
        return Err("invalidCover".into());
    }
    let source = root.join(cover);
    let mut file = open_regular_file(&source)?;
    let metadata = file
        .metadata()
        .map_err(|error| path_error(&source, error))?;
    if metadata.len() > 20 * 1024 * 1024 {
        return Err(AppError::new("coverTooLarge").param("path", source.display()));
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|error| path_error(&source, error))?;
    let mut reader = ImageReader::with_format(std::io::Cursor::new(&bytes), ImageFormat::Jpeg);
    let mut limits = Limits::default();
    limits.max_image_width = Some(300);
    limits.max_image_height = Some(300);
    limits.max_alloc = Some(4 * 1024 * 1024);
    reader.limits(limits);
    reader
        .decode()
        .map_err(|error| path_error(&source, error))?;

    Ok((bytes, metadata))
}

fn add_cover(
    inventory: &mut Inventory,
    package: &Package,
    root: &Path,
    cover: &str,
    sku: u32,
) -> Result<(), Box<Issue>> {
    let invalid_cover = |error| Box::new(Issue::cause("invalidCover", "cover", error));
    let source = root.join(cover);
    let (_, metadata) = managed_cover(root, cover).map_err(invalid_cover)?;

    let store = support_segment(&package.store);
    let name = support_segment(&package.name);
    let name = if name.is_empty() { "Package" } else { &name };
    let filename = format!("{store}_{sku}_{name}.jpg");
    validate_segment(&filename).map_err(invalid_cover)?;
    let collision = |path: &str| Box::new(Issue::value("coverCollision", "cover", path));
    let runtime = match inventory.names.get("RUNTIME") {
        Some((path, false)) => return Err(collision(path)),
        Some((path, true)) => path.as_str(),
        None => "Runtime",
    };
    let support = match inventory.names.get("RUNTIME/SUPPORT") {
        Some((path, false)) => return Err(collision(path)),
        Some((path, true)) => path.clone(),
        None => format!("{runtime}/Support"),
    };
    let relative = format!("{support}/{filename}");
    if let Some((path, directory)) = inventory.names.get(&case_key(&relative)) {
        if *directory {
            return Err(collision(path));
        }
        let original = format!("Content/{path}");
        inventory.members.retain(|member| member.name != original);
    }
    inventory.members.push(Member {
        name: format!("Content/{relative}"),
        source,
        metadata,
    });
    Ok(())
}

pub fn export(
    package: &Package,
    root: &Path,
    daz_folders: &[String],
    destination: &Path,
    approved_stamp: Option<&str>,
    cancelled: &AtomicBool,
    progress: impl Fn(Phase, u64, u64),
) -> Result<PathBuf, AppError> {
    check_cancelled(cancelled)?;
    progress(Phase::Validating, 0, 0);
    let plan = plan(package, root, daz_folders, cancelled);
    check_cancelled(cancelled)?;
    if let Some(issue) = plan
        .preview
        .issues
        .iter()
        .find(|issue| issue.severity == Severity::Error)
    {
        return Err(issue.error.clone());
    }
    let filename = plan.preview.filename.as_ref().ok_or("invalidOutputFile")?;
    let guid = plan.guid.ok_or("invalidGuid")?;
    check_chain(destination)?;
    let output_path = destination.join(filename);
    let destination =
        fs::canonicalize(destination).map_err(|error| path_error(destination, error))?;
    let root = fs::canonicalize(root).map_err(|error| path_error(root, error))?;
    if path_contains(&root, &destination) {
        return Err(AppError::new("outputInsideWorkspace").param("path", destination.display()));
    }
    let target = destination.join(filename);
    if output_stamp(&target)?.as_deref() != approved_stamp {
        return Err(AppError::new("outputChanged").param("path", target.display()));
    }

    let mut temp = tempfile::Builder::new()
        .prefix(".dim-creator-")
        .suffix(".zip.tmp")
        .tempfile_in(&destination)
        .map_err(|error| path_error(&destination, error))?;
    let manifest = manifest_xml(guid, &plan.members, cancelled)
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    let supplement = supplement_xml(package, plan.store_index.as_deref())
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    let total = plan.preview.size;
    let mut written = 0;
    let mut buffer = vec![0u8; BUFFER_SIZE];
    {
        let mut archive = ZipWriter::new(temp.as_file_mut());
        for (name, bytes) in [(MANIFEST, &manifest), (SUPPLEMENT, &supplement)] {
            start_member(&mut archive, name, bytes.len() as u64)?;
            archive
                .write_all(bytes)
                .map_err(|error| path_error(&target, error))?;
        }
        progress(Phase::Writing, 0, total);
        for member in &plan.members {
            check_cancelled(cancelled)?;
            let mut source = open_regular_file(&member.source)?;
            let opened = source
                .metadata()
                .map_err(|error| path_error(&member.source, error))?;
            if !same_file_state(&member.metadata, &opened) {
                return Err(AppError::new("sourceChanged").param("path", member.source.display()));
            }
            start_member(&mut archive, &member.name, opened.len())?;
            let mut remaining = opened.len();
            while remaining > 0 {
                check_cancelled(cancelled)?;
                let read_len = remaining.min(buffer.len() as u64) as usize;
                source
                    .read_exact(&mut buffer[..read_len])
                    .map_err(|error| path_error(&member.source, error))?;
                archive
                    .write_all(&buffer[..read_len])
                    .map_err(|error| path_error(&target, error))?;
                remaining -= read_len as u64;
                written += read_len as u64;
                if written == total
                    || written / (4 * 1024 * 1024)
                        != (written - read_len as u64) / (4 * 1024 * 1024)
                {
                    progress(Phase::Writing, written, total);
                }
            }
            let after = source
                .metadata()
                .map_err(|error| path_error(&member.source, error))?;
            if !same_file_state(&opened, &after) {
                return Err(AppError::new("sourceChanged").param("path", member.source.display()));
            }
        }
        archive
            .finish()
            .map_err(|error| path_error(&target, error))?;
    }
    progress(Phase::Verifying, written, total);
    verify_archive(
        temp.as_file_mut(),
        &plan.members,
        &manifest,
        &supplement,
        cancelled,
        &mut buffer,
    )?;
    temp.as_file()
        .sync_all()
        .map_err(|error| path_error(&target, error))?;
    progress(Phase::Publishing, written, total);
    check_cancelled(cancelled)?;
    check_chain(&destination)?;
    publish(temp, &target, approved_stamp)?;
    Ok(output_path)
}

fn start_member(archive: &mut ZipWriter<&mut File>, name: &str, size: u64) -> Result<(), AppError> {
    archive
        .start_file(
            name,
            SimpleFileOptions::default()
                .compression_method(CompressionMethod::Deflated)
                .large_file(size >= 3 * 1024 * 1024 * 1024),
        )
        .map_err(|error| AppError::diagnostic("ioError", error).param("path", name))
}

fn verify_archive(
    file: &mut File,
    members: &[Member],
    manifest: &[u8],
    supplement: &[u8],
    cancelled: &AtomicBool,
    buffer: &mut [u8],
) -> Result<(), AppError> {
    check_cancelled(cancelled)?;
    file.seek(SeekFrom::Start(0))
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    let mut archive =
        ZipArchive::new(file).map_err(|error| AppError::diagnostic("operationError", error))?;
    if archive.len() != members.len() + 2 {
        return Err("invalidArchive".into());
    }
    // Compare generated XML byte-for-byte and read all entries to EOF for ZIP CRC validation.
    let expected = [(MANIFEST, manifest), (SUPPLEMENT, supplement)]
        .into_iter()
        .map(|(name, bytes)| (name, bytes.len() as u64, Some(bytes)))
        .chain(
            members
                .iter()
                .map(|member| (member.name.as_str(), member.metadata.len(), None)),
        );
    for (index, (name, size, xml)) in expected.enumerate() {
        let mut item = archive
            .by_index(index)
            .map_err(|error| AppError::diagnostic("operationError", error))?;
        if item
            .name()
            .map_err(|error| AppError::diagnostic("invalidArchive", error))?
            != name
            || item.size() != size
            || item.is_dir()
        {
            return Err(AppError::new("invalidArchive").param("name", name));
        }
        let mut offset = 0;
        loop {
            check_cancelled(cancelled)?;
            let read = item
                .read(buffer)
                .map_err(|error| AppError::diagnostic("ioError", error).param("path", name))?;
            if read == 0 {
                break;
            }
            if let Some(xml) = xml
                && xml.get(offset..offset + read) != Some(&buffer[..read])
            {
                return Err(AppError::new("invalidArchive").param("name", name));
            }
            offset += read;
        }
        if offset as u64 != size {
            return Err(AppError::new("invalidArchive").param("name", name));
        }
    }
    Ok(())
}

fn manifest_xml(guid: Uuid, members: &[Member], cancelled: &AtomicBool) -> io::Result<Vec<u8>> {
    let mut writer = xml_document("DAZInstallManifest")?;
    xml_value(&mut writer, "GlobalID", &guid.to_string())?;
    for member in members {
        if cancelled.load(Ordering::Relaxed) {
            return Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "resultCancelled",
            ));
        }
        let mut file = BytesStart::new("File");
        file.push_attribute(("TARGET", "Content"));
        file.push_attribute(("ACTION", "Install"));
        file.push_attribute(("VALUE", member.name.as_str()));
        writer.write_event(Event::Empty(file))?;
    }
    writer.write_event(Event::End(BytesEnd::new("DAZInstallManifest")))?;
    Ok(writer.into_inner())
}

fn supplement_xml(package: &Package, store_index: Option<&str>) -> io::Result<Vec<u8>> {
    let mut writer = xml_document("ProductSupplement")?;
    xml_value(&mut writer, "ProductName", package.name.trim())?;
    if let Some(index) = store_index {
        xml_value(&mut writer, "ProductStoreIDX", index)?;
    }
    xml_value(&mut writer, "InstallTypes", "Content")?;
    xml_value(&mut writer, "ProductTags", &package.tags.join(","))?;
    writer.write_event(Event::End(BytesEnd::new("ProductSupplement")))?;
    Ok(writer.into_inner())
}

fn xml_document(root: &str) -> io::Result<Writer<Vec<u8>>> {
    let mut writer = Writer::new_with_indent(Vec::new(), b' ', 2);
    writer.write_event(Event::Decl(BytesDecl::new("1.0", Some("UTF-8"), None)))?;
    let mut start = BytesStart::new(root);
    start.push_attribute(("VERSION", "0.1"));
    writer.write_event(Event::Start(start))?;
    Ok(writer)
}

fn xml_value(writer: &mut Writer<Vec<u8>>, element: &str, value: &str) -> io::Result<()> {
    let mut element = BytesStart::new(element);
    element.push_attribute(("VALUE", value));
    writer.write_event(Event::Empty(element))
}

fn existing_output(path: &Path) -> Result<Option<File>, AppError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !is_link(&metadata) => {
            open_replaceable_file(path).map(Some)
        }
        Ok(_) => Err(AppError::new("invalidOutputFile").param("path", path.display())),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(path_error(path, error)),
    }
}

pub fn output_stamp(path: &Path) -> Result<Option<String>, AppError> {
    existing_output(path)?
        .as_ref()
        .map(|file| file_stamp(file).map_err(|error| path_error(path, error)))
        .transpose()
}

fn file_stamp(file: &File) -> io::Result<String> {
    let metadata = file.metadata()?;
    let mut hash = DefaultHasher::new();
    hash_file_identity(file, &mut hash)?;
    metadata.len().hash(&mut hash);
    metadata.modified().ok().hash(&mut hash);
    metadata.created().ok().hash(&mut hash);
    Ok(format!("{:016x}", hash.finish()))
}

fn publish(temp: NamedTempFile, path: &Path, original: Option<&str>) -> Result<(), AppError> {
    if let Some(original) = original {
        let current = existing_output(path)?
            .ok_or_else(|| AppError::new("outputChanged").param("path", path.display()))?;
        if file_stamp(&current).map_err(|error| path_error(path, error))? != original {
            return Err(AppError::new("outputChanged").param("path", path.display()));
        }
        // Windows rejects replacing a destination with an open read handle.
        drop(current);
        temp.persist(path)
    } else {
        temp.persist_noclobber(path)
    }
    .map(|_| ())
    .map_err(|error| path_error(path, error.error))
}

fn same_file_state(expected: &Metadata, actual: &Metadata) -> bool {
    expected.len() == actual.len()
        && expected.modified().ok() == actual.modified().ok()
        && expected.created().ok() == actual.created().ok()
}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), AppError> {
    if cancelled.load(Ordering::Relaxed) {
        return Err("resultCancelled".into());
    }
    Ok(())
}

fn path_error(path: &Path, error: impl std::fmt::Display) -> AppError {
    AppError::diagnostic("ioError", error).param("path", path.display())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn example() -> Package {
        Package {
            id: Uuid::new_v4().to_string(),
            name: "G8.1 – Café & Coat".into(),
            store: "DAZ 3D".into(),
            prefix: "IM".into(),
            auto_prefix: true,
            sku: "00001234".into(),
            part: "07".into(),
            guid: "27f56798-f387-47cc-8ac6-609f89de9136".into(),
            tags: vec!["DAZStudio4_5".into()],
            cover: None,
            clean_support: true,
            excluded: Vec::new(),
        }
    }

    fn create_content(root: &Path, name: &str) {
        let path = root.join("Content").join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, "fixture").unwrap();
    }

    #[test]
    fn configurable_roots_control_preview_and_the_actual_export() {
        let root = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        create_content(root.path(), "Custom Root/asset.duf");
        create_content(root.path(), "readme.txt");
        let package = example();
        let custom = vec!["custom root".into()];
        assert!(
            preview(&package, root.path(), &[])
                .issues
                .iter()
                .any(|issue| issue.error.code == "noDazContent")
        );
        assert!(
            export(
                &package,
                root.path(),
                &[],
                destination.path(),
                None,
                &AtomicBool::new(false),
                |_, _, _| {}
            )
            .is_err()
        );
        let preview = preview(&package, root.path(), &custom);
        assert!(
            !preview
                .issues
                .iter()
                .any(|issue| issue.severity == Severity::Error)
        );
        let output = export(
            &package,
            root.path(),
            &custom,
            destination.path(),
            None,
            &AtomicBool::new(false),
            |_, _, _| {},
        )
        .unwrap();
        let archive = ZipArchive::new(File::open(output).unwrap()).unwrap();
        assert_eq!(archive.len(), preview.entries.len());
        assert!(
            archive
                .file_names()
                .any(|name| name.unwrap() == "Content/readme.txt")
        );
    }

    #[test]
    fn exports_the_inventory_shown_in_preview_with_escaped_metadata() {
        let root = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        create_content(root.path(), "People/Coat & Boots.duf");
        create_content(root.path(), "People/Drafts/Not For Release.duf");
        create_content(root.path(), "Runtime/Support/old.dsx");
        let mut package = example();
        package.excluded.push("People/Drafts".into());
        let cover = format!("cover-{}.jpg", Uuid::new_v4());
        image::RgbImage::new(4, 4)
            .save(root.path().join(&cover))
            .unwrap();
        package.cover = Some(cover);

        let planned = preview(&package, root.path(), &crate::model::default_daz_folders());
        assert_eq!(
            planned.filename.as_deref(),
            Some("IM00001234-07_G81CafCoat.zip")
        );
        assert!(
            !planned
                .issues
                .iter()
                .any(|issue| issue.severity == Severity::Error)
        );
        let output = export(
            &package,
            root.path(),
            &crate::model::default_daz_folders(),
            destination.path(),
            None,
            &AtomicBool::new(false),
            |_, _, _| {},
        )
        .unwrap();
        let mut archive = zip::ZipArchive::new(File::open(output).unwrap()).unwrap();
        let mut names: Vec<_> = archive
            .file_names()
            .map(|name| name.unwrap().into_owned())
            .collect();
        names.sort();
        let mut expected = planned.entries;
        expected.sort();
        assert_eq!(names, expected);
        assert!(names.contains(&"Content/Runtime/Support/DAZ_3D_1234_G8_1_Caf_Coat.jpg".into()));
        assert!(
            !names
                .iter()
                .any(|name| name.contains("old.dsx") || name.contains("Drafts"))
        );

        let mut manifest = String::new();
        archive
            .by_name("Manifest.dsx")
            .unwrap()
            .read_to_string(&mut manifest)
            .unwrap();
        assert!(manifest.contains("VALUE=\"Content/People/Coat &amp; Boots.duf\""));
        let mut supplement = String::new();
        archive
            .by_name("Supplement.dsx")
            .unwrap()
            .read_to_string(&mut supplement)
            .unwrap();
        assert!(supplement.contains("VALUE=\"G8.1 – Café &amp; Coat\""));
        assert!(supplement.contains("<ProductStoreIDX VALUE=\"1234-7\"/>"));
        assert!(root.path().join("Content/Runtime/Support/old.dsx").exists());
    }

    #[test]
    fn custom_prefix_leaves_product_store_index_to_dim() {
        let root = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        create_content(root.path(), "data/test.dsf");
        let mut package = example();
        package.prefix = "LOCAL".into();
        package.tags.clear();

        let output = export(
            &package,
            root.path(),
            &crate::model::default_daz_folders(),
            destination.path(),
            None,
            &AtomicBool::new(false),
            |_, _, _| {},
        )
        .unwrap();
        let mut archive = zip::ZipArchive::new(File::open(output).unwrap()).unwrap();
        let mut supplement = String::new();
        archive
            .by_name("Supplement.dsx")
            .unwrap()
            .read_to_string(&mut supplement)
            .unwrap();
        assert!(!supplement.contains("ProductStoreIDX"));
    }

    #[test]
    fn cover_alone_does_not_qualify_as_daz_content() {
        let root = tempfile::tempdir().unwrap();
        create_content(root.path(), "README.txt");
        create_content(root.path(), "Runtime/Support/old.dsx");
        let mut package = example();
        let cover = format!("cover-{}.jpg", Uuid::new_v4());
        image::RgbImage::new(4, 4)
            .save(root.path().join(&cover))
            .unwrap();
        package.cover = Some(cover);
        assert!(
            preview(&package, root.path(), &crate::model::default_daz_folders())
                .issues
                .iter()
                .any(|issue| issue.error.code == "noDazContent")
        );
    }

    #[test]
    fn a_corrupt_managed_cover_blocks_the_package() {
        let root = tempfile::tempdir().unwrap();
        create_content(root.path(), "People/test.duf");
        let mut package = example();
        let cover = format!("cover-{}.jpg", Uuid::new_v4());
        fs::write(root.path().join(&cover), b"corrupt image").unwrap();
        package.cover = Some(cover);
        assert!(
            preview(&package, root.path(), &crate::model::default_daz_folders())
                .issues
                .iter()
                .any(|issue| issue.error.code == "invalidCover")
        );
    }

    #[test]
    fn cancelled_export_and_unapproved_export_preserve_existing_zip() {
        let root = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        create_content(root.path(), "Runtime/test.dsf");
        let package = example();
        let target = destination.path().join(
            preview(&package, root.path(), &crate::model::default_daz_folders())
                .filename
                .unwrap(),
        );
        fs::write(&target, "original archive").unwrap();

        assert!(
            export(
                &package,
                root.path(),
                &crate::model::default_daz_folders(),
                destination.path(),
                None,
                &AtomicBool::new(false),
                |_, _, _| {}
            )
            .is_err()
        );
        let cancelled = AtomicBool::new(false);
        assert!(
            export(
                &package,
                root.path(),
                &crate::model::default_daz_folders(),
                destination.path(),
                output_stamp(&target).unwrap().as_deref(),
                &cancelled,
                |phase, _, _| {
                    if phase == Phase::Verifying {
                        cancelled.store(true, Ordering::Relaxed);
                    }
                }
            )
            .is_err()
        );
        assert_eq!(fs::read_to_string(&target).unwrap(), "original archive");
        assert_eq!(fs::read_dir(destination.path()).unwrap().count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn replacement_rejects_an_open_writer_even_when_the_size_is_unchanged() {
        let root = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        create_content(root.path(), "Runtime/test.dsf");
        let package = example();
        let target = destination.path().join(
            preview(&package, root.path(), &crate::model::default_daz_folders())
                .filename
                .unwrap(),
        );
        fs::write(&target, b"old zip").unwrap();
        let approved = output_stamp(&target).unwrap().unwrap();
        let mut writer = fs::OpenOptions::new().write(true).open(&target).unwrap();
        writer.write_all(b"new zip").unwrap();
        let cancelled = AtomicBool::new(false);
        assert!(
            export(
                &package,
                root.path(),
                &crate::model::default_daz_folders(),
                destination.path(),
                Some(&approved),
                &cancelled,
                |_, _, _| {}
            )
            .is_err()
        );
        drop(writer);
        assert_eq!(fs::read(&target).unwrap(), b"new zip");

        let current = output_stamp(&target).unwrap().unwrap();
        export(
            &package,
            root.path(),
            &crate::model::default_daz_folders(),
            destination.path(),
            Some(&current),
            &cancelled,
            |_, _, _| {},
        )
        .unwrap();
        assert!(zip::ZipArchive::new(File::open(target).unwrap()).is_ok());
    }

    #[test]
    fn streams_large_members_and_can_cancel_during_writing() {
        let root = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        let package = example();
        let name = "Runtime/Textures/large.bin";
        create_content(root.path(), name);
        let mut file = File::create(root.path().join("Content").join(name)).unwrap();
        let chunk = [123u8; BUFFER_SIZE];
        for _ in 0..80 {
            file.write_all(&chunk).unwrap();
        }
        drop(file);

        let cancelled = AtomicBool::new(false);
        let result = export(
            &package,
            root.path(),
            &crate::model::default_daz_folders(),
            destination.path(),
            None,
            &cancelled,
            |phase, done, total| {
                if phase == Phase::Writing && 0 < done && done < total {
                    cancelled.store(true, Ordering::Relaxed);
                }
            },
        );
        assert_eq!(result.unwrap_err().code, "resultCancelled");
        assert_eq!(fs::read_dir(destination.path()).unwrap().count(), 0);

        let output = export(
            &package,
            root.path(),
            &crate::model::default_daz_folders(),
            destination.path(),
            None,
            &AtomicBool::new(false),
            |_, _, _| {},
        )
        .unwrap();
        let mut archive = ZipArchive::new(File::open(output).unwrap()).unwrap();
        let mut member = archive.by_name(&format!("Content/{name}")).unwrap();
        let mut actual = [0; BUFFER_SIZE];
        for _ in 0..80 {
            member.read_exact(&mut actual).unwrap();
            assert_eq!(actual, chunk);
        }
        assert_eq!(member.read(&mut actual).unwrap(), 0);
    }
}
