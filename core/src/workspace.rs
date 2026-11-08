use crate::error::AppError;
use crate::paths::relative_segments;
use std::{
    collections::{HashMap, HashSet, hash_map::DefaultHasher},
    fs::{self, File},
    hash::{Hash, Hasher},
    io::{self, BufReader, Read, Write},
    path::{Path, PathBuf},
    time::SystemTime,
};

use image::{ImageFormat, ImageReader, Limits};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use tempfile::{NamedTempFile, TempDir};
use uuid::Uuid;

use crate::{
    filesystem::{
        case_key, check_chain, entry_name, hash_file_identity, is_link, open_regular_file,
        open_replaceable_file, path_contains, validate_segment,
    },
    model::{Package, Settings, check_id},
};

const VERSION: u64 = 1;
const MAX_COVER_BYTES: u64 = 20 * 1024 * 1024;
const MAX_COVER_PIXELS: u64 = 40_000_000;

mod archive_import;
mod template_import;
pub use template_import::{TemplatePreview, TemplateResult, TemplateSkipReason};
mod operations;
pub use archive_import::*;
mod synchronization;
pub use synchronization::{SyncFailure, SyncPlan, SyncResult, fingerprint};
mod worklist;
pub use operations::{
    FileChange, FileChangeKind, FileMutation, FileResult, FileStatus, TransferMode,
};

pub struct Workspace {
    root: PathBuf,
    worklist: std::sync::Mutex<Option<worklist::WorklistSession>>,
}

#[derive(Debug, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct FileEntry {
    pub name: String,
    pub path: String,
    pub directory: bool,
    pub size: u64,
    pub kind: String,
}

#[derive(Clone, Copy, Deserialize, PartialEq, Eq, ts_rs::TS)]
#[serde(rename_all = "lowercase")]
pub enum CopyPolicy {
    Replace,
    Skip,
    Cancel,
}

#[derive(Debug, Serialize, Deserialize, ts_rs::TS)]
pub struct CopyConflict {
    pub path: String,
    pub stamp: String,
}

impl Workspace {
    pub fn new(root: PathBuf) -> Result<Self, AppError> {
        if !root.is_absolute()
            || root
                .components()
                .any(|item| matches!(item, std::path::Component::ParentDir))
        {
            return Err("invalidAbsolutePath".into());
        }
        let existing = root
            .ancestors()
            .find(|ancestor| {
                !matches!(
                    fs::symlink_metadata(ancestor),
                    Err(error) if error.kind() == io::ErrorKind::NotFound
                )
            })
            .ok_or("workspaceUnavailable")?;
        check_chain(existing)?;
        fs::create_dir_all(&root).map_err(io_error)?;
        check_chain(&root)?;
        let packages = root.join("packages");
        match fs::create_dir(&packages) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(io_error(error)),
        }
        require_directory(&packages)?;
        Ok(Self {
            root,
            worklist: std::sync::Mutex::new(None),
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn catalog(&self) -> Result<(Vec<Package>, Vec<AppError>), AppError> {
        let directory = self.root.join("packages");
        require_directory(&directory)?;
        let mut packages = Vec::new();
        let mut warnings = Vec::new();
        for entry in fs::read_dir(&directory).map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            let Some(id) = entry
                .file_name()
                .into_string()
                .ok()
                .filter(|id| check_id(id).is_ok())
            else {
                continue;
            };
            match self.load_package(&id) {
                Ok((package, recovered)) => {
                    let created = fs::symlink_metadata(entry.path())
                        .and_then(|metadata| metadata.created())
                        .unwrap_or(SystemTime::UNIX_EPOCH);
                    if recovered {
                        warnings.push(
                            AppError::new("packageBackupLoaded").param("name", &package.name),
                        );
                    }
                    packages.push((created, package));
                }
                Err(error) => warnings.push(
                    AppError::new("packageUnavailable")
                        .param("id", id)
                        .caused_by([error]),
                ),
            }
        }
        packages.sort_by(|(created_a, a), (created_b, b)| {
            created_a.cmp(created_b).then(a.id.cmp(&b.id))
        });
        warnings.sort_by(|a, b| a.params.cmp(&b.params));
        Ok((
            packages.into_iter().map(|(_, package)| package).collect(),
            warnings,
        ))
    }

    pub fn package_dir(&self, id: &str) -> Result<PathBuf, AppError> {
        check_id(id)?;
        let directory = self.root.join("packages").join(id);
        require_directory(&directory)?;
        Ok(directory)
    }

    pub fn package(&self, id: &str) -> Result<Package, AppError> {
        self.load_package(id).map(|(package, _)| package)
    }

    fn load_package(&self, id: &str) -> Result<(Package, bool), AppError> {
        load_record(
            &self.package_dir(id)?.join("package.json"),
            |package: &Package| package.validate_storage(id),
        )
    }

    pub fn settings(&self) -> Result<Settings, AppError> {
        let path = self.root.join("settings.json");
        match load_record(&path, Settings::validate).map(|(settings, _)| settings.normalized()) {
            Err(_) if !exists(&path)? && !exists(&backup_path(&path))? => Ok(Settings::default()),
            result => result,
        }
    }

    pub fn save_settings(&self, settings: &Settings) -> Result<Settings, AppError> {
        let settings = settings.normalized();
        settings.validate()?;
        save_record(
            &self.root.join("settings.json"),
            &settings,
            Settings::validate,
        )?;
        Ok(settings)
    }

    pub fn create(&self, name: &str) -> Result<Package, AppError> {
        let name = name.trim();
        if name.is_empty() || name.chars().count() > 160 {
            return Err("invalidProductName".into());
        }
        let package = Package::new(name, &self.settings()?)?;
        let packages = self.root.join("packages");
        require_directory(&packages)?;
        let staging = tempfile::Builder::new()
            .prefix(".package-")
            .tempdir_in(&packages)
            .map_err(io_error)?;
        fs::create_dir(staging.path().join("Content")).map_err(io_error)?;
        save_record(&staging.path().join("package.json"), &package, |_| Ok(()))?;
        fs::rename(staging.path(), packages.join(&package.id)).map_err(io_error)?;
        Ok(package)
    }

    pub fn save(&self, package: &Package) -> Result<(), AppError> {
        let persisted = self.package(&package.id)?;
        if package.cover != persisted.cover {
            return Err("managedCoverOnly".into());
        }
        self.save_package(package)
    }

    fn save_package(&self, package: &Package) -> Result<(), AppError> {
        package.validate_storage(&package.id)?;
        let directory = self.package_dir(&package.id)?;
        let path = directory.join("package.json");
        let validate = |value: &Package| value.validate_storage(&package.id);
        let old_cover = read_record(&backup_path(&path), &validate)
            .ok()
            .and_then(|(_, previous)| previous.cover);
        let primary = read_record(&path, &validate)
            .ok()
            .map(|(_, previous)| previous);
        save_record(&path, package, validate)?;
        if let Some(old_cover) = old_cover
            && primary.is_some()
            && Some(&old_cover)
                != primary
                    .as_ref()
                    .and_then(|previous| previous.cover.as_ref())
            && Some(&old_cover) != package.cover.as_ref()
        {
            let _ = fs::remove_file(directory.join(old_cover));
        }
        Ok(())
    }

    pub fn folder(&self, id: &str, folder: &str) -> Result<PathBuf, AppError> {
        let mut path = self.package_dir(id)?.join("Content");
        for segment in relative_segments(folder)? {
            validate_segment(segment)?;
            path.push(segment);
        }
        require_directory(&path)?;
        Ok(path)
    }

    pub fn files(&self, id: &str, folder: &str) -> Result<Vec<FileEntry>, AppError> {
        let directory = self.folder(id, folder)?;
        let mut files = Vec::new();
        for entry in fs::read_dir(directory).map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            let metadata = fs::symlink_metadata(entry.path()).map_err(io_error)?;
            let path = entry.path();
            let name = entry_name(&path)?;
            let directory = metadata.is_dir() && !is_link(&metadata);
            let kind = if is_link(&metadata) {
                "link".into()
            } else if directory {
                "folder".into()
            } else {
                path.extension()
                    .and_then(|value| value.to_str())
                    .unwrap_or("file")
                    .to_owned()
            };
            files.push(FileEntry {
                name: name.into(),
                path: relative_join(folder, name),
                directory,
                size: if metadata.is_file() && !is_link(&metadata) {
                    metadata.len()
                } else {
                    0
                },
                kind,
            });
        }
        files.sort_by_key(|entry| (!entry.directory, case_key(&entry.name)));
        Ok(files)
    }

    pub fn create_folder(&self, id: &str, folder: &str, name: &str) -> Result<(), AppError> {
        validate_segment(name)?;
        let path = self.folder(id, folder)?;
        if children(&path)?.contains_key(&case_key(name)) {
            return Err(AppError::new("fileNameExists").param("name", name));
        }
        fs::create_dir(path.join(name)).map_err(io_error)
    }

    pub fn copy_conflicts(
        &self,
        id: &str,
        folder: &str,
        sources: &[PathBuf],
    ) -> Result<Vec<CopyConflict>, AppError> {
        let destination = self.folder(id, folder)?;
        let operations = copy_plan(&destination, &self.package_dir(id)?, sources)?;
        Ok(operations
            .into_iter()
            .filter_map(|operation| {
                operation.stamp.map(|stamp| CopyConflict {
                    path: copy_relative(&operation.target, &destination),
                    stamp,
                })
            })
            .collect())
    }

    pub fn copy_approved(
        &self,
        id: &str,
        folder: &str,
        sources: &[PathBuf],
        policy: CopyPolicy,
        approved: &[CopyConflict],
    ) -> Result<(), AppError> {
        let destination = self.folder(id, folder)?;
        let package = self.package_dir(id)?;
        let operations = copy_plan(&destination, &package, sources)?;
        if policy != CopyPolicy::Skip {
            let approved: HashMap<_, _> = approved
                .iter()
                .map(|conflict| (case_key(&conflict.path), &conflict.stamp))
                .collect();
            if operations.iter().any(|operation| {
                operation.stamp.as_ref().is_some_and(|stamp| {
                    let path = case_key(&copy_relative(&operation.target, &destination));
                    policy == CopyPolicy::Cancel || approved.get(&path).copied() != Some(stamp)
                })
            }) {
                return Err("unresolvedFileConflicts".into());
            }
        }
        let staging = tempfile::Builder::new()
            .prefix(".copy-")
            .tempdir_in(package)
            .map_err(io_error)?;
        let mut prepared = Vec::new();
        for operation in operations {
            if operation.stamp.is_some() && policy == CopyPolicy::Skip {
                continue;
            }
            let staged = staging.path().join(format!("new-{}", prepared.len()));
            stage_item(&operation.source, &staged)?;
            prepared.push((operation, staged));
        }
        publish_copy(staging, prepared, || Ok(()))
    }

    pub fn set_cover(&self, id: &str, source: Option<&Path>) -> Result<Package, AppError> {
        let mut package = self.package(id)?;
        let directory = self.package_dir(id)?;
        let mut new_path = None;
        package.cover = if let Some(source) = source {
            let image = read_image(source)?;
            let name = format!("cover-{}.jpg", Uuid::new_v4());
            let path = directory.join(&name);
            let mut temporary = NamedTempFile::new_in(&directory).map_err(io_error)?;
            image
                .thumbnail(300, 300)
                .to_rgb8()
                .write_to(temporary.as_file_mut(), ImageFormat::Jpeg)
                .map_err(|error| AppError::diagnostic("operationError", error))?;
            temporary.as_file().sync_all().map_err(io_error)?;
            temporary
                .persist_noclobber(&path)
                .map_err(|error| io_error(error.error))?;
            new_path = Some(path);
            Some(name)
        } else {
            None
        };
        if let Err(error) = self.save_package(&package) {
            if let Some(path) = new_path {
                let _ = fs::remove_file(path);
            }
            return Err(error);
        }
        Ok(package)
    }

    pub fn cover(&self, id: &str) -> Result<Option<Vec<u8>>, AppError> {
        let Some(name) = self.package(id)?.cover else {
            return Ok(None);
        };
        let path = self.package_dir(id)?.join(name);
        let mut bytes = Vec::new();
        open_regular_file(&path)?
            .take(MAX_COVER_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(io_error)?;
        if bytes.len() as u64 > MAX_COVER_BYTES {
            return Err("coverTooLarge".into());
        }
        Ok(Some(bytes))
    }
}

fn io_error(error: io::Error) -> AppError {
    AppError::diagnostic("ioError", error)
}

fn require_directory(path: &Path) -> Result<(), AppError> {
    check_chain(path)?;
    if !fs::symlink_metadata(path).map_err(io_error)?.is_dir() {
        return Err(AppError::new("notDirectory").param("path", path.display()));
    }
    Ok(())
}

fn exists(path: &Path) -> Result<bool, AppError> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(io_error(error)),
    }
}

fn backup_path(path: &Path) -> PathBuf {
    path.with_extension("backup.json")
}

enum RecordError {
    Missing,
    Invalid(AppError),
    Protected(AppError),
}

impl RecordError {
    fn message(self) -> AppError {
        match self {
            Self::Missing => "recordMissing".into(),
            Self::Invalid(error) | Self::Protected(error) => error,
        }
    }
}

fn read_record<T: DeserializeOwned>(
    path: &Path,
    validate: &impl Fn(&T) -> Result<(), AppError>,
) -> Result<(Vec<u8>, T), RecordError> {
    if !exists(path).map_err(RecordError::Protected)? {
        return Err(RecordError::Missing);
    }
    let mut bytes = Vec::new();
    open_replaceable_file(path)
        .map_err(RecordError::Protected)?
        .read_to_end(&mut bytes)
        .map_err(|error| RecordError::Protected(io_error(error)))?;
    let invalid = |error: AppError| {
        RecordError::Invalid(
            AppError::new("recordInvalid")
                .param("path", path.display())
                .caused_by([error]),
        )
    };
    let mut document: serde_json::Value = serde_json::from_slice(&bytes)
        .map_err(|error| invalid(AppError::diagnostic("recordInvalid", error)))?;
    let version = document
        .get("version")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| invalid("recordInvalid".into()))?;
    if version != VERSION {
        return Err(RecordError::Protected(
            AppError::new("unsupportedRecordVersion")
                .param("version", version)
                .param("path", path.display()),
        ));
    }
    let data = document
        .get_mut("data")
        .ok_or_else(|| invalid("recordInvalid".into()))?;
    let data = serde_json::from_value(data.take())
        .map_err(|error| invalid(AppError::diagnostic("recordInvalid", error)))?;
    validate(&data).map_err(invalid)?;
    Ok((bytes, data))
}

fn load_record<T: DeserializeOwned>(
    path: &Path,
    validate: impl Fn(&T) -> Result<(), AppError>,
) -> Result<(T, bool), AppError> {
    match read_record(path, &validate) {
        Ok((_, data)) => Ok((data, false)),
        Err(RecordError::Protected(error)) => Err(error),
        Err(primary) => match read_record(&backup_path(path), &validate) {
            Ok((_, data)) => Ok((data, true)),
            Err(RecordError::Missing) => Err(primary.message()),
            Err(backup) => Err(AppError::new("recordRecoveryFailed")
                .caused_by([primary.message(), backup.message()])),
        },
    }
}

fn save_record<T: Serialize + DeserializeOwned>(
    path: &Path,
    data: &T,
    validate: impl Fn(&T) -> Result<(), AppError>,
) -> Result<(), AppError> {
    let backup = backup_path(path);
    // Backups are part of the on-disk contract. An older app must not overwrite newer records.
    let backup_state = match read_record(&backup, &validate) {
        Err(RecordError::Protected(error)) => return Err(error),
        state => state,
    };
    match read_record(path, &validate) {
        Ok((previous, _)) => {
            if matches!(backup_state, Err(RecordError::Invalid(_))) {
                preserve_damaged_record(&backup)?;
            }
            atomic_write(&backup, &previous)?;
        }
        Err(RecordError::Missing) => {}
        Err(RecordError::Invalid(_)) if backup_state.is_ok() => preserve_damaged_record(path)?,
        Err(error) => return Err(error.message()),
    }
    atomic_write(path, &record_bytes(data)?)
}

fn record_bytes(data: &impl Serialize) -> Result<Vec<u8>, AppError> {
    serde_json::to_vec_pretty(&serde_json::json!({ "version": VERSION, "data": data }))
        .map_err(|error| AppError::diagnostic("operationError", error))
}

fn preserve_damaged_record(path: &Path) -> Result<(), AppError> {
    let parent = path.parent().ok_or("invalidPackagePath")?;
    let mut source = open_replaceable_file(path)?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".damaged-")
        .suffix(".json")
        .tempfile_in(parent)
        .map_err(io_error)?;
    io::copy(&mut source, temporary.as_file_mut()).map_err(io_error)?;
    temporary.as_file().sync_all().map_err(io_error)?;
    temporary.keep().map_err(|error| io_error(error.error))?;
    Ok(())
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), AppError> {
    let parent = path.parent().ok_or("invalidPackagePath")?;
    require_directory(parent)?;
    let mut temporary = NamedTempFile::new_in(parent).map_err(io_error)?;
    temporary.write_all(bytes).map_err(io_error)?;
    temporary.as_file().sync_all().map_err(io_error)?;
    if exists(path)? {
        check_chain(path)?;
    }
    temporary
        .persist(path)
        .map_err(|error| io_error(error.error))?;
    Ok(())
}

fn read_image(source: &Path) -> Result<image::DynamicImage, AppError> {
    let file = open_regular_file(source)?;
    if file.metadata().map_err(io_error)?.len() > MAX_COVER_BYTES {
        return Err("coverTooLarge".into());
    }
    let mut reader = ImageReader::new(BufReader::new(file))
        .with_guessed_format()
        .map_err(io_error)?;
    if !matches!(
        reader.format(),
        Some(ImageFormat::Jpeg | ImageFormat::Png | ImageFormat::WebP)
    ) {
        return Err("unsupportedCoverFormat".into());
    }
    let mut limits = Limits::default();
    limits.max_image_width = Some(16_384);
    limits.max_image_height = Some(16_384);
    limits.max_alloc = Some(192 * 1024 * 1024);
    reader.limits(limits);
    let mut decoder = reader
        .into_decoder()
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    use image::ImageDecoder;
    let (width, height) = decoder.dimensions();
    if u64::from(width) * u64::from(height) > MAX_COVER_PIXELS {
        return Err("coverDimensions".into());
    }
    let orientation = decoder
        .orientation()
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    let mut image = image::DynamicImage::from_decoder(decoder)
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    image.apply_orientation(orientation);
    Ok(image)
}

fn relative_join(parent: &str, name: &str) -> String {
    if parent.is_empty() {
        name.into()
    } else {
        format!("{parent}/{name}")
    }
}

fn copy_relative(target: &Path, destination: &Path) -> String {
    target
        .strip_prefix(destination)
        .expect("target under destination")
        .to_string_lossy()
        .replace('\\', "/")
}

struct CopyOperation {
    source: PathBuf,
    target: PathBuf,
    stamp: Option<String>,
}

fn children(directory: &Path) -> Result<HashMap<String, PathBuf>, AppError> {
    require_directory(directory)?;
    let mut entries = HashMap::new();
    for entry in fs::read_dir(directory).map_err(io_error)? {
        let path = entry.map_err(io_error)?.path();
        if entries.insert(case_key(entry_name(&path)?), path).is_some() {
            return Err(AppError::new("caseCollision").param("path", directory.display()));
        }
    }
    Ok(entries)
}

fn regular_item(path: &Path) -> Result<fs::Metadata, AppError> {
    check_chain(path)?;
    let metadata = fs::symlink_metadata(path).map_err(io_error)?;
    if is_link(&metadata) || !(metadata.is_file() || metadata.is_dir()) {
        return Err(AppError::new("unsupportedEntry").param("path", path.display()));
    }
    Ok(metadata)
}

fn copy_plan(
    destination: &Path,
    package: &Path,
    sources: &[PathBuf],
) -> Result<Vec<CopyOperation>, AppError> {
    let mut operations = Vec::new();
    let mut selected = HashSet::new();
    let existing = children(destination)?;
    let canonical_package = package.canonicalize().map_err(io_error)?;
    for source in sources {
        let name = entry_name(source)?;
        let key = case_key(name);
        if !selected.insert(key.clone()) {
            return Err(AppError::new("duplicateTransferName").param("name", name));
        }
        let metadata = regular_item(source)?;
        let target = destination.join(name);
        if metadata.is_dir()
            && path_contains(
                &source.canonicalize().map_err(io_error)?,
                &canonical_package,
            )
        {
            return Err("recursiveTransfer".into());
        }
        collect_operations(source, target, existing.get(&key), &mut operations)?;
    }
    Ok(operations)
}

fn collect_operations(
    source: &Path,
    target: PathBuf,
    existing: Option<&PathBuf>,
    operations: &mut Vec<CopyOperation>,
) -> Result<(), AppError> {
    let source_metadata = regular_item(source)?;
    let Some(existing) = existing else {
        operations.push(CopyOperation {
            source: source.into(),
            target,
            stamp: None,
        });
        return Ok(());
    };
    let target_metadata = regular_item(existing)?;
    let source_absolute = source.canonicalize().map_err(io_error)?;
    let target_absolute = existing.canonicalize().map_err(io_error)?;
    if path_contains(&source_absolute, &target_absolute)
        || path_contains(&target_absolute, &source_absolute)
    {
        return Err("recursiveTransfer".into());
    }

    if source_metadata.is_dir() && target_metadata.is_dir() {
        let existing_entries = children(existing)?;
        let mut incoming = children(source)?.into_values().collect::<Vec<_>>();
        incoming.sort();
        for child in incoming {
            let name = entry_name(&child)?;
            collect_operations(
                &child,
                existing.join(name),
                existing_entries.get(&case_key(name)),
                operations,
            )?;
        }
    } else {
        operations.push(CopyOperation {
            source: source.into(),
            target: existing.clone(),
            stamp: Some(target_stamp(existing)?),
        });
    }
    Ok(())
}

fn stage_item(source: &Path, staged: &Path) -> Result<(), AppError> {
    let metadata = regular_item(source)?;
    if metadata.is_dir() {
        fs::create_dir(staged).map_err(io_error)?;
        for child in children(source)?.into_values() {
            stage_item(&child, &staged.join(entry_name(&child)?))?;
        }
    } else {
        let mut source_file = open_regular_file(source)?;
        let mut target_file = File::create_new(staged).map_err(io_error)?;
        let size = io::copy(&mut source_file, &mut target_file).map_err(io_error)?;
        target_file.sync_all().map_err(io_error)?;
        let after = source_file.metadata().map_err(io_error)?;
        if size != metadata.len()
            || after.len() != metadata.len()
            || after.modified().ok() != metadata.modified().ok()
        {
            return Err(AppError::new("sourceChanged").param("path", source.display()));
        }
    }
    Ok(())
}

fn target_stamp(path: &Path) -> Result<String, AppError> {
    let mut stamp = DefaultHasher::new();
    stamp_tree(path, &regular_item(path)?, &mut stamp)?;
    Ok(format!("{:016x}", stamp.finish()))
}

fn stamp_tree(
    path: &Path,
    metadata: &fs::Metadata,
    stamp: &mut DefaultHasher,
) -> Result<(), AppError> {
    let link = is_link(metadata);
    path.file_name().hash(stamp);
    metadata.is_dir().hash(stamp);
    metadata.is_file().hash(stamp);
    link.hash(stamp);
    metadata.len().hash(stamp);
    metadata.created().ok().hash(stamp);
    metadata.modified().ok().hash(stamp);
    metadata.permissions().readonly().hash(stamp);
    if metadata.is_file() && !link {
        let file = open_replaceable_file(path)?;
        hash_file_identity(&file, stamp).map_err(io_error)?;
    }
    if metadata.is_dir() && !link {
        let mut entries = children(path)?.into_values().collect::<Vec<_>>();
        entries.sort();
        entries.len().hash(stamp);
        for entry in entries {
            stamp_tree(
                &entry,
                &fs::symlink_metadata(&entry).map_err(io_error)?,
                stamp,
            )?;
        }
    }
    Ok(())
}

struct PublishedCopy {
    target: PathBuf,
    previous: PathBuf,
    backup: Option<PathBuf>,
    published: bool,
}

fn publish_copy(
    staging: TempDir,
    prepared: Vec<(CopyOperation, PathBuf)>,
    finish: impl FnOnce() -> Result<(), AppError>,
) -> Result<(), AppError> {
    let mut journal = Vec::with_capacity(prepared.len());
    let result = (|| -> Result<(), AppError> {
        for (operation, staged) in prepared {
            let parent = operation.target.parent().ok_or("fileUnavailable")?;
            let key = case_key(entry_name(&operation.target)?);
            let existing = children(parent)?;
            let backup = if let Some(stamp) = &operation.stamp {
                let current = existing.get(&key).ok_or("destinationChanged")?;
                let _guard = regular_item(current)?
                    .is_file()
                    .then(|| open_replaceable_file(current))
                    .transpose()?;
                if target_stamp(current)? != *stamp {
                    return Err("destinationChanged".into());
                }
                let backup = staging.path().join(format!("previous-{}", journal.len()));
                fs::rename(current, &backup).map_err(io_error)?;
                Some(backup)
            } else {
                if existing.contains_key(&key) {
                    return Err("destinationChanged".into());
                }
                None
            };
            journal.push(PublishedCopy {
                previous: existing
                    .get(&key)
                    .cloned()
                    .unwrap_or_else(|| operation.target.clone()),
                target: operation.target,
                backup,
                published: false,
            });
            let record = journal.last_mut().expect("new copy journal record");
            if fs::symlink_metadata(&staged).map_err(io_error)?.is_dir() {
                fs::rename(staged, &record.target).map_err(io_error)?;
            } else {
                tempfile::TempPath::try_from_path(staged)
                    .map_err(io_error)?
                    .persist_noclobber(&record.target)
                    .map_err(|error| io_error(error.error))?;
            }
            record.published = true;
        }
        finish()
    })();
    if let Err(error) = result {
        let mut rollback_errors = Vec::new();
        for (index, record) in journal.into_iter().enumerate().rev() {
            let restore = (|| -> Result<(), AppError> {
                require_directory(record.target.parent().ok_or("fileUnavailable")?)?;
                if record.published {
                    fs::rename(
                        &record.target,
                        staging.path().join(format!("discard-{index}")),
                    )
                    .map_err(io_error)?;
                }
                if let Some(backup) = record.backup {
                    if exists(&record.target)? {
                        return Err(AppError::new("destinationChanged")
                            .param("path", record.target.display()));
                    }
                    fs::rename(backup, record.previous).map_err(io_error)?;
                }
                Ok(())
            })();
            if let Err(error) = restore {
                rollback_errors.push(error);
            }
        }
        if !rollback_errors.is_empty() {
            let recovery = staging.keep();
            return Err(AppError::new("rollbackFailed")
                .param("path", recovery.display())
                .caused_by(std::iter::once(error).chain(rollback_errors)));
        }
        return Err(error);
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn internal_file_operations_follow_exclusions_and_keep_copy_sources() {
        let (_dir, workspace) = workspace();
        let mut package = workspace.create("Files").unwrap();
        let root = workspace.folder(&package.id, "").unwrap();
        fs::create_dir_all(root.join("People/Source")).unwrap();
        fs::create_dir(root.join("Props")).unwrap();
        fs::write(root.join("People/Source/a.duf"), "a").unwrap();
        package.excluded = vec!["People/Source/a.duf".into()];
        workspace.save(&package).unwrap();
        let copied = workspace
            .transfer_files(
                &package.id,
                "People",
                &["People/Source".into()],
                TransferMode::Copy,
                CopyPolicy::Cancel,
                &[],
            )
            .unwrap();
        assert!(root.join("People/Source/a.duf").is_file());
        assert!(root.join("People/Source (2)/a.duf").is_file());
        assert!(
            copied
                .package
                .excluded
                .contains(&"People/Source (2)/a.duf".into())
        );
        let moved = workspace
            .transfer_files(
                &package.id,
                "Props",
                &["People/Source".into()],
                TransferMode::Move,
                CopyPolicy::Cancel,
                &[],
            )
            .unwrap();
        assert!(!root.join("People/Source").exists());
        assert!(
            moved
                .package
                .excluded
                .contains(&"Props/Source/a.duf".into())
        );
        let renamed = workspace
            .rename_entry(&package.id, "Props/Source", "source")
            .unwrap();
        assert!(
            renamed
                .package
                .excluded
                .contains(&"Props/source/a.duf".into())
        );
        assert!(
            workspace
                .transfer_files(
                    &package.id,
                    "Props/source",
                    &["Props".into()],
                    TransferMode::Move,
                    CopyPolicy::Cancel,
                    &[]
                )
                .is_err()
        );
        let trashed = workspace
            .trash_entries(&package.id, &["Props/source".into()], |path| {
                fs::rename(path, root.join("trashed")).map_err(io_error)
            })
            .unwrap();
        assert!(
            !trashed
                .package
                .excluded
                .iter()
                .any(|path| path.starts_with("Props/source"))
        );
        assert!(root.join("trashed/a.duf").is_file());
    }

    #[test]
    fn daz_folder_settings_preserve_legacy_values_and_validate_names() {
        let (_dir, workspace) = workspace();
        let mut legacy = serde_json::to_value(Settings::default()).unwrap();
        legacy.as_object_mut().unwrap().remove("dazFolders");
        legacy["language"] = "en".into();
        legacy["tags"] = serde_json::json!(["Custom"]);
        legacy["defaultTags"] = serde_json::json!([]);
        fs::write(
            workspace.root().join("settings.json"),
            serde_json::to_vec(&serde_json::json!({"version":1,"data":legacy})).unwrap(),
        )
        .unwrap();
        let settings = workspace.settings().unwrap();
        let value = serde_json::to_value(&settings).unwrap();
        assert_eq!(value["dazFolders"].as_array().unwrap().len(), 15);
        assert_eq!(settings.language, "en");
        assert_eq!(settings.tags, ["Custom"]);
        for folders in [
            serde_json::json!(["People", "people"]),
            serde_json::json!(["../People"]),
            serde_json::json!(["CON"]),
        ] {
            let mut value = value.clone();
            value["dazFolders"] = folders;
            assert!(
                workspace
                    .save_settings(&serde_json::from_value(value).unwrap())
                    .is_err()
            );
        }
        let mut value = value;
        value["dazFolders"] = serde_json::json!([]);
        workspace
            .save_settings(&serde_json::from_value(value).unwrap())
            .unwrap();
        assert_eq!(
            serde_json::to_value(workspace.settings().unwrap()).unwrap()["dazFolders"],
            serde_json::json!([])
        );
    }

    #[test]
    fn inspector_width_is_optional_persisted_and_validated() {
        let (_dir, workspace) = workspace();
        let mut legacy = serde_json::to_value(Settings::default()).unwrap();
        legacy.as_object_mut().unwrap().remove("inspectorWidth");
        legacy["language"] = "en".into();
        legacy["tags"] = serde_json::json!(["Custom"]);
        legacy["defaultTags"] = serde_json::json!([]);
        fs::write(
            workspace.root().join("settings.json"),
            serde_json::to_vec(&serde_json::json!({"version":1,"data":legacy})).unwrap(),
        )
        .unwrap();
        let settings = workspace.settings().unwrap();
        let mut value = serde_json::to_value(&settings).unwrap();
        assert!(value.as_object().unwrap().contains_key("inspectorWidth"));
        assert!(value["inspectorWidth"].is_null());
        value["inspectorWidth"] = 620.into();
        workspace
            .save_settings(&serde_json::from_value(value.clone()).unwrap())
            .unwrap();
        let reopened = Workspace::new(workspace.root().into())
            .unwrap()
            .settings()
            .unwrap();
        assert_eq!(
            serde_json::to_value(&reopened).unwrap()["inspectorWidth"],
            620
        );
        assert_eq!(reopened.tags, settings.tags);
        assert_eq!(reopened.daz_folders, settings.daz_folders);
        assert_eq!(reopened.language, "en");
        value["inspectorWidth"] = 200.into();
        assert!(
            workspace
                .save_settings(&serde_json::from_value(value.clone()).unwrap())
                .is_err()
        );
        value["inspectorWidth"] = serde_json::Value::Null;
        workspace
            .save_settings(&serde_json::from_value(value).unwrap())
            .unwrap();
        assert!(
            serde_json::to_value(workspace.settings().unwrap()).unwrap()["inspectorWidth"]
                .is_null()
        );
    }

    fn workspace() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(dir.path().join("app")).unwrap();
        (dir, workspace)
    }

    #[test]
    fn drafts_reopen_independently_and_recover_from_backup() {
        let (dir, workspace) = workspace();
        let mut draft = workspace.create("Example").unwrap();
        assert_eq!(draft.part, "01");
        assert!(draft.clean_support);
        assert_eq!(draft.prefix, "IM");
        let second = workspace.create("Other").unwrap();
        assert_ne!(draft.guid, second.guid);
        let root = workspace.package_dir(&draft.id).unwrap();

        draft.guid = "half typed".into();
        workspace.save(&draft).unwrap();
        assert_eq!(workspace.package(&draft.id).unwrap(), draft);
        fs::write(root.join("package.json"), "{broken").unwrap();

        let reopened = Workspace::new(dir.path().join("app")).unwrap();
        let recovered = reopened.package(&draft.id).unwrap();
        assert_ne!(recovered.guid, draft.guid);
        assert_eq!(recovered.name, "Example");
        assert_eq!(reopened.package(&second.id).unwrap(), second);
        assert_eq!(reopened.catalog().unwrap().1.len(), 1);
        reopened.save(&recovered).unwrap();
        assert!(reopened.catalog().unwrap().1.is_empty());
        let damaged = fs::read_dir(root)
            .unwrap()
            .flatten()
            .find(|entry| entry.file_name().to_string_lossy().starts_with(".damaged-"))
            .unwrap();
        assert_eq!(fs::read_to_string(damaged.path()).unwrap(), "{broken");
    }

    #[test]
    fn configured_defaults_are_copied_without_changing_existing_packages() {
        let (dir, workspace) = workspace();
        let original = workspace.create("Factory").unwrap();
        assert_eq!(original.store, "DAZ 3D");
        assert_eq!(original.prefix, "IM");
        assert!(original.auto_prefix);
        assert_eq!(original.tags, ["DAZStudio4_5"]);
        let original_path = workspace
            .package_dir(&original.id)
            .unwrap()
            .join("package.json");
        let original_bytes = fs::read(&original_path).unwrap();
        let mut json = serde_json::to_value(workspace.settings().unwrap()).unwrap();
        json["defaultStore"] = "Renderosity".into();
        json["defaultTags"] = serde_json::json!(["General", "Blender"]);
        workspace
            .save_settings(&serde_json::from_value(json.clone()).unwrap())
            .unwrap();
        let reopened = Workspace::new(dir.path().join("app")).unwrap();
        let customized = reopened.create("Custom").unwrap();
        assert_eq!(customized.store, "Renderosity");
        assert_eq!(customized.prefix, "RO");
        assert!(customized.auto_prefix);
        assert_eq!(customized.tags, ["General", "Blender"]);
        json["stores"][1]["name"] = "Renamed".into();
        json["stores"][1]["prefix"] = "NEW".into();
        json["defaultStore"] = "Renamed".into();
        json["defaultTags"] = serde_json::json!([]);
        reopened
            .save_settings(&serde_json::from_value(json).unwrap())
            .unwrap();
        let empty_tags = reopened.create("No tags").unwrap();
        assert_eq!(empty_tags.store, "Renamed");
        assert_eq!(empty_tags.prefix, "NEW");
        assert!(empty_tags.tags.is_empty());
        assert_eq!(reopened.package(&customized.id).unwrap(), customized);
        assert_eq!(reopened.package(&original.id).unwrap(), original);
        assert_eq!(fs::read(original_path).unwrap(), original_bytes);
        fs::write(reopened.root().join("settings.json"), "{broken").unwrap();
        let recovered = reopened.create("Recovered settings").unwrap();
        assert_eq!(recovered.store, "Renderosity");
        assert_eq!(recovered.tags, ["General", "Blender"]);
    }

    #[test]
    fn invalid_defaults_preserve_settings_and_loading_resolves_reference_spelling() {
        let (_dir, workspace) = workspace();
        let settings = workspace.settings().unwrap();
        workspace.save_settings(&settings).unwrap();
        let path = workspace.root().join("settings.json");
        let before = fs::read(&path).unwrap();
        for (field, value) in [
            ("defaultStore", serde_json::json!("Deleted")),
            ("defaultTags", serde_json::json!(["Deleted"])),
            ("stores", serde_json::json!([])),
        ] {
            let mut invalid = serde_json::to_value(&settings).unwrap();
            invalid[field] = value;
            assert!(
                workspace
                    .save_settings(&serde_json::from_value(invalid).unwrap())
                    .is_err()
            );
            assert_eq!(fs::read(&path).unwrap(), before);
        }
        let mut document: serde_json::Value = serde_json::from_slice(&before).unwrap();
        document["data"]["defaultStore"] = " daz 3d ".into();
        document["data"]["defaultTags"] = serde_json::json!([" dazstudio4_5 "]);
        fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
        assert_eq!(workspace.settings().unwrap(), settings);
        assert_eq!(workspace.create("Normalized").unwrap().prefix, "IM");
    }

    #[test]
    fn newer_settings_are_never_hidden_by_an_older_backup() {
        let (_dir, workspace) = workspace();
        let defaults = workspace.settings().unwrap();
        workspace.save_settings(&defaults).unwrap();
        workspace.save_settings(&defaults).unwrap();
        let path = workspace.root.join("settings.json");
        fs::write(&path, r#"{"version":2}"#).unwrap();
        assert!(workspace.settings().is_err());
        assert!(workspace.save_settings(&defaults).is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), r#"{"version":2}"#);
    }

    #[test]
    fn a_corrupt_package_does_not_hide_other_packages() {
        let (_dir, workspace) = workspace();
        let healthy = workspace.create("Healthy").unwrap();
        let corrupt = workspace.create("Corrupt").unwrap();
        let path = workspace
            .package_dir(&corrupt.id)
            .unwrap()
            .join("package.json");
        fs::write(&path, "damaged").unwrap();
        let (available, warnings) = workspace.catalog().unwrap();
        assert_eq!(available, [healthy]);
        assert_eq!(warnings.len(), 1);
        assert_eq!(warnings[0].params["id"], corrupt.id);
        assert_eq!(fs::read_to_string(path).unwrap(), "damaged");
    }

    #[test]
    fn folder_merge_respects_conflict_policy_and_preserves_sources() {
        let (dir, workspace) = workspace();
        let draft = workspace.create("Example").unwrap();
        workspace.create_folder(&draft.id, "", "Runtime").unwrap();
        let destination = workspace.folder(&draft.id, "Runtime").unwrap();
        fs::write(destination.join("same.txt"), "existing").unwrap();
        fs::write(destination.join("keep.txt"), "keep").unwrap();

        let source = dir.path().join("Runtime");
        fs::create_dir(&source).unwrap();
        fs::write(source.join("SAME.txt"), "new").unwrap();
        fs::write(source.join("more.txt"), "more").unwrap();
        let sources = [source.clone()];

        let approved = workspace.copy_conflicts(&draft.id, "", &sources).unwrap();
        assert_eq!(approved[0].path, "Runtime/same.txt");
        assert_eq!(approved.len(), 1);
        assert!(
            workspace
                .copy_approved(&draft.id, "", &sources, CopyPolicy::Cancel, &[])
                .is_err()
        );
        assert!(!destination.join("more.txt").exists());

        workspace
            .copy_approved(&draft.id, "", &sources, CopyPolicy::Skip, &[])
            .unwrap();
        assert_eq!(
            fs::read_to_string(destination.join("same.txt")).unwrap(),
            "existing"
        );
        assert_eq!(
            fs::read_to_string(destination.join("more.txt")).unwrap(),
            "more"
        );

        assert!(
            workspace
                .copy_approved(&draft.id, "", &sources, CopyPolicy::Replace, &approved)
                .is_err()
        );
        assert_eq!(
            fs::read_to_string(destination.join("same.txt")).unwrap(),
            "existing"
        );
        let approved = workspace.copy_conflicts(&draft.id, "", &sources).unwrap();
        workspace
            .copy_approved(&draft.id, "", &sources, CopyPolicy::Replace, &approved)
            .unwrap();
        assert_eq!(
            fs::read_to_string(destination.join("same.txt")).unwrap(),
            "new"
        );
        assert_eq!(
            fs::read_to_string(destination.join("keep.txt")).unwrap(),
            "keep"
        );
        assert_eq!(fs::read_to_string(source.join("SAME.txt")).unwrap(), "new");
        assert_eq!(workspace.files(&draft.id, "Runtime").unwrap().len(), 3);
    }

    #[test]
    fn unsafe_sources_leave_existing_files_untouched() {
        let (dir, workspace) = workspace();
        let draft = workspace.create("Example").unwrap();
        let destination = workspace.folder(&draft.id, "").unwrap();
        fs::write(destination.join("first.txt"), "existing").unwrap();
        let source = dir.path().join("first.txt");
        fs::write(&source, "new").unwrap();
        let missing = dir.path().join("missing.txt");

        let approved = workspace
            .copy_conflicts(&draft.id, "", std::slice::from_ref(&source))
            .unwrap();
        assert!(
            workspace
                .copy_approved(
                    &draft.id,
                    "",
                    &[source, missing],
                    CopyPolicy::Replace,
                    &approved
                )
                .is_err()
        );
        assert_eq!(
            fs::read_to_string(destination.join("first.txt")).unwrap(),
            "existing"
        );
        assert!(workspace.folder(&draft.id, "../").is_err());
        assert!(workspace.create_folder(&draft.id, "", "AUX.txt").is_err());
        assert!(workspace.package_dir("../app").is_err());
    }

    #[test]
    fn replacing_a_directory_rechecks_its_nested_entries() {
        let (dir, workspace) = workspace();
        let package = workspace.create("Example").unwrap();
        workspace
            .create_folder(&package.id, "", "collision")
            .unwrap();
        let target = workspace.folder(&package.id, "collision").unwrap();
        fs::write(target.join("existing.txt"), "old").unwrap();
        let source = dir.path().join("collision");
        fs::write(&source, "new").unwrap();
        let sources = [source];
        let approval = workspace.copy_conflicts(&package.id, "", &sources).unwrap();
        fs::write(target.join("existing.txt"), "changed after approval").unwrap();
        assert!(
            workspace
                .copy_approved(&package.id, "", &sources, CopyPolicy::Replace, &approval)
                .is_err()
        );
        assert_eq!(
            fs::read_to_string(target.join("existing.txt")).unwrap(),
            "changed after approval"
        );
    }

    #[test]
    fn publication_rechecks_destinations_after_staging() {
        let (dir, workspace) = workspace();
        let package = workspace.create("Example").unwrap();
        let package_dir = workspace.package_dir(&package.id).unwrap();
        let destination = workspace.folder(&package.id, "").unwrap();
        let target = destination.join("target.txt");
        fs::write(&target, "old").unwrap();
        let source = dir.path().join("target.txt");
        fs::write(&source, "new").unwrap();
        let mut operations = copy_plan(&destination, &package_dir, &[source]).unwrap();
        let operation = operations.pop().unwrap();
        let staging = tempfile::tempdir_in(package_dir).unwrap();
        let staged = staging.path().join("incoming");
        stage_item(&operation.source, &staged).unwrap();

        fs::write(&target, "changed after staging").unwrap();
        assert!(publish_copy(staging, vec![(operation, staged)], || Ok(())).is_err());
        assert_eq!(fs::read_to_string(target).unwrap(), "changed after staging");
    }

    #[test]
    fn invalid_cover_replacement_keeps_the_existing_cover() {
        let (dir, workspace) = workspace();
        let draft = workspace.create("Example").unwrap();
        let source = dir.path().join("cover.png");
        image::RgbImage::new(400, 200).save(&source).unwrap();
        let covered = workspace.set_cover(&draft.id, Some(&source)).unwrap();
        let bytes = workspace.cover(&draft.id).unwrap().unwrap();
        let thumbnail = image::load_from_memory(&bytes).unwrap();
        assert_eq!((thumbnail.width(), thumbnail.height()), (300, 150));

        let broken = dir.path().join("broken.jpg");
        fs::write(&broken, "broken").unwrap();
        assert!(workspace.set_cover(&draft.id, Some(&broken)).is_err());
        assert_eq!(workspace.package(&draft.id).unwrap().cover, covered.cover);
        assert_eq!(workspace.cover(&draft.id).unwrap().unwrap(), bytes);
        workspace.set_cover(&draft.id, None).unwrap();
        assert!(workspace.cover(&draft.id).unwrap().is_none());
    }
}
