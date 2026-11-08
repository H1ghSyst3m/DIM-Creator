use super::*;
use crate::filesystem::{open_regular_file, require_space};
use crate::model::Settings;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashSet,
    hash::{Hash, Hasher},
    path::PathBuf,
};
use tempfile::TempDir;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveRoot {
    pub id: String,
    pub path: String,
    pub folders: Vec<String>,
    pub files: u32,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveContent {
    pub id: String,
    pub archive_id: String,
    pub archive_name: String,
    pub origin: Vec<String>,
    pub roots: Vec<ArchiveRoot>,
    pub suggested_group: Option<String>,
    pub issue: Option<AppError>,
    pub classification: ArchiveClassification,
    pub suggested_action: ImportAction,
    pub template_issue: Option<AppError>,
    pub template_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub enum ArchiveClassification {
    Content,
    Template,
    NoContent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub enum ImportAction {
    Package,
    Template,
    Exclude,
}

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveSessionView {
    pub id: String,
    pub contents: Vec<ArchiveContent>,
    pub template_directory_issue: Option<AppError>,
}

#[derive(Debug, Clone, Copy, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub enum ArchivePhase {
    Starting,
    Copy,
    Inventory,
    Security,
    Extract,
    Recognize,
    Import,
    Template,
}

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveProgress {
    pub phase: ArchivePhase,
    pub name: String,
    pub entries: u64,
    pub bytes: u64,
}

struct VerifiedContent {
    view: ArchiveContent,
    directory: PathBuf,
    inventory: Arc<Vec<ArchiveEntry>>,
}

struct Source {
    path: PathBuf,
    stamp: u64,
    _lock: File,
}

pub(crate) struct VerifiedArchive {
    pub path: PathBuf,
    pub name: String,
    pub source_location: String,
    pub bytes: u64,
    stamp: u64,
    issue: Option<AppError>,
}

pub struct ArchiveSession {
    id: String,
    _temporary: TempDir,
    contents: Vec<VerifiedContent>,
    sources: Vec<Source>,
    archives: HashMap<String, VerifiedArchive>,
    daz_folders: Vec<String>,
    template_dir: Option<PathBuf>,
    auto_save_templates: bool,
    template_directory_issue: Option<AppError>,
}

fn source_stamp(path: &Path) -> Result<u64, AppError> {
    let file = open_regular_file(path)?;
    let metadata = file
        .metadata()
        .map_err(|error| AppError::diagnostic("ioError", error))?;
    let mut hash = std::collections::hash_map::DefaultHasher::new();
    metadata.len().hash(&mut hash);
    metadata
        .modified()
        .map_err(|error| AppError::diagnostic("ioError", error))?
        .hash(&mut hash);
    crate::filesystem::hash_file_identity(&file, &mut hash)
        .map_err(|error| AppError::diagnostic("ioError", error))?;
    Ok(hash.finish())
}

pub(crate) fn template_directory(path: &Path, workspace: &Path) -> Result<PathBuf, AppError> {
    check_chain(path)?;
    if !path.is_dir() {
        return Err(AppError::new("templateDirectoryInvalid").param("path", path.display()));
    }
    let resolved = path
        .canonicalize()
        .map_err(|error| AppError::diagnostic("ioError", error))?;
    let workspace = workspace
        .canonicalize()
        .map_err(|error| AppError::diagnostic("ioError", error))?;
    if crate::filesystem::path_contains(&workspace, &resolved) {
        return Err("templateInsideWorkspace".into());
    }
    Ok(resolved)
}

fn archive_name(path: &str) -> bool {
    Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|ext| matches!(ext.to_ascii_lowercase().as_str(), "zip" | "rar" | "7z"))
}

fn template(name: &str) -> bool {
    name.split(|c: char| !c.is_ascii_alphanumeric())
        .any(|part| part.eq_ignore_ascii_case("template") || part.eq_ignore_ascii_case("templates"))
}

fn roots(
    entries: &[ArchiveEntry],
    daz: &[String],
    control: &ArchiveControl,
) -> Result<Vec<ArchiveRoot>, AppError> {
    let daz: HashSet<_> = daz.iter().map(|name| case_key(name)).collect();
    let mut roots = std::collections::BTreeMap::<String, ArchiveRoot>::new();
    for entry in entries
        .iter()
        .filter(|entry| !entry.directory && !archive_name(&entry.path))
    {
        control.check()?;
        let parts: Vec<_> = entry.path.split('/').collect();
        for (index, part) in parts.iter().take(parts.len() - 1).enumerate() {
            if daz.contains(&case_key(part)) {
                let path = parts[..index].join("/");
                let root = roots.entry(path.clone()).or_insert_with(|| ArchiveRoot {
                    id: Uuid::new_v4().to_string(),
                    path,
                    folders: Vec::new(),
                    files: 0,
                    bytes: 0,
                });
                if !root.folders.iter().any(|folder| folder == part) {
                    root.folders.push((*part).to_owned());
                }
            }
        }
    }
    for entry in entries
        .iter()
        .filter(|entry| !entry.directory && !archive_name(&entry.path))
    {
        control.check()?;
        let mut parent = entry.path.rsplit_once('/').map_or("", |(parent, _)| parent);
        loop {
            if let Some(root) = roots.get_mut(parent) {
                root.files += 1;
                root.bytes += entry.size;
            }
            if parent.is_empty() {
                break;
            }
            parent = parent.rsplit_once('/').map_or("", |(parent, _)| parent);
        }
    }
    for root in roots.values_mut() {
        root.folders.sort();
    }
    Ok(roots.into_values().collect())
}

impl ArchiveSession {
    pub fn analyze(
        sources: &[PathBuf],
        temporary_parent: &Path,
        settings: &Settings,
        control: &ArchiveControl,
        mut progress: impl FnMut(ArchiveProgress),
    ) -> Result<Self, AppError> {
        if sources.is_empty() || sources.len() > 100 {
            return Err("archiveLimit".into());
        }
        check_chain(temporary_parent)?;
        let temporary = tempfile::Builder::new()
            .prefix(".archive-session-")
            .tempdir_in(temporary_parent)
            .map_err(|error| AppError::diagnostic("ioError", error))?;
        let mut session = Self {
            id: Uuid::new_v4().to_string(),
            _temporary: temporary,
            contents: Vec::new(),
            sources: Vec::new(),
            archives: HashMap::new(),
            daz_folders: settings.daz_folders.clone(),
            template_dir: settings.template_dir.as_ref().map(PathBuf::from),
            auto_save_templates: settings.auto_save_templates,
            template_directory_issue: settings
                .template_dir
                .as_ref()
                .map_or(Some("templateDirectoryRequired".into()), |path| {
                    template_directory(Path::new(path), temporary_parent).err()
                }),
        };
        let mut archives = 0;
        for source in sources {
            control.check()?;
            let name = source
                .file_name()
                .and_then(|value| value.to_str())
                .ok_or("invalidFilename")?
                .to_owned();
            let stamp = source_stamp(source)?;
            let input = open_regular_file(source)?;
            let source_lock = input
                .try_clone()
                .map_err(|error| AppError::diagnostic("ioError", error))?;
            let size = input
                .metadata()
                .map_err(|error| AppError::diagnostic("ioError", error))?
                .len();
            require_space(session._temporary.path(), size.saturating_add(2 * GIB))?;
            progress(ArchiveProgress {
                phase: ArchivePhase::Copy,
                name: name.clone(),
                entries: control.entries(),
                bytes: 0,
            });
            let copy = session
                ._temporary
                .path()
                .join(format!("source-{}", Uuid::new_v4()));
            let mut target = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&copy)
                .map_err(|error| AppError::diagnostic("ioError", error))?;
            control.set_phase(ArchivePhase::Copy);
            let mut input = input;
            let mut copied = 0_u64;
            let mut buffer = [0_u8; 64 * 1024];
            loop {
                control.check()?;
                let count = input
                    .read(&mut buffer)
                    .map_err(|error| control.decode_error(error))?;
                if count == 0 {
                    break;
                }
                target
                    .write_all(&buffer[..count])
                    .map_err(|error| AppError::diagnostic("ioError", error))?;
                copied += count as u64;
                control.add_processed(count as u64);
            }
            target
                .sync_all()
                .map_err(|error| AppError::diagnostic("ioError", error))?;
            drop(target);
            if copied != size || source_stamp(source)? != stamp {
                return Err("archiveSourceChanged".into());
            }
            session.sources.push(Source {
                path: source.clone(),
                stamp,
                _lock: source_lock,
            });
            let collector = Uuid::new_v4().to_string();
            session.visit(
                &copy,
                name,
                Vec::new(),
                source.to_string_lossy().into_owned(),
                &collector,
                0,
                &mut archives,
                control,
                &mut progress,
            )?;
        }
        let mut counts = HashMap::new();
        for item in &session.contents {
            if item.view.issue.is_none() && !item.view.roots.is_empty() {
                *counts.entry(item.view.suggested_group.clone()).or_insert(0) += 1;
            }
        }
        for item in &mut session.contents {
            if counts.get(&item.view.suggested_group).copied().unwrap_or(0) < 2 {
                item.view.suggested_group = None;
            }
        }
        Ok(session)
    }

    #[allow(clippy::too_many_arguments)]
    fn visit(
        &mut self,
        source: &Path,
        name: String,
        origin: Vec<String>,
        source_location: String,
        collector: &str,
        depth: usize,
        archives: &mut usize,
        control: &ArchiveControl,
        progress: &mut impl FnMut(ArchiveProgress),
    ) -> Result<(), AppError> {
        control.check()?;
        *archives += 1;
        if *archives > 100 {
            return Err("archiveLimit".into());
        }
        let id = Uuid::new_v4().to_string();
        let directory = self._temporary.path().join(&id);
        fs::create_dir(&directory).map_err(|error| AppError::diagnostic("ioError", error))?;
        let mut view = ArchiveContent {
            id: id.clone(),
            archive_id: id.clone(),
            archive_name: name.clone(),
            origin: origin.clone(),
            roots: Vec::new(),
            suggested_group: Some(collector.into()),
            issue: None,
            classification: ArchiveClassification::NoContent,
            suggested_action: ImportAction::Exclude,
            template_issue: None,
            template_path: self.template_dir.as_ref().map(|dir| dir.join(&name)),
        };
        self.archives.insert(
            id.clone(),
            VerifiedArchive {
                path: source.to_owned(),
                name: name.clone(),
                source_location: source_location.clone(),
                bytes: open_regular_file(source)?
                    .metadata()
                    .map_err(|error| AppError::diagnostic("ioError", error))?
                    .len(),
                stamp: source_stamp(source)?,
                issue: None,
            },
        );
        progress(ArchiveProgress {
            phase: ArchivePhase::Inventory,
            name: name.clone(),
            entries: control.entries(),
            bytes: control.bytes(),
        });
        let inventory = if depth > 3 {
            Err(AppError::new("archiveDepth"))
        } else {
            validate_segment(&name).and_then(|()| extract_archive(source, &directory, control))
        };
        let inventory = match inventory {
            Ok(entries) => entries,
            Err(error) => {
                control.check()?;
                fs::remove_dir_all(&directory)
                    .map_err(|error| AppError::diagnostic("ioError", error))?;
                view.issue = Some(error.clone());
                self.archives.get_mut(&id).unwrap().issue = Some(error);
                self.contents.push(VerifiedContent {
                    view,
                    directory,
                    inventory: Arc::new(Vec::new()),
                });
                return Ok(());
            }
        };
        control.set_phase(ArchivePhase::Recognize);
        view.roots = roots(&inventory, &self.daz_folders, control)?;
        let mut inventory = inventory;
        inventory.sort_by(|a, b| a.path.cmp(&b.path));
        let inventory = Arc::new(inventory);
        let nested: Vec<_> = inventory
            .iter()
            .filter(|entry| !entry.directory && archive_name(&entry.path))
            .map(|entry| entry.path.clone())
            .collect();
        view.classification = if !view.roots.is_empty() {
            ArchiveClassification::Content
        } else if template(&name) {
            ArchiveClassification::Template
        } else {
            ArchiveClassification::NoContent
        };
        view.suggested_action = match view.classification {
            ArchiveClassification::Content => ImportAction::Package,
            ArchiveClassification::Template
                if self.auto_save_templates && self.template_dir.is_some() =>
            {
                ImportAction::Template
            }
            _ => ImportAction::Exclude,
        };
        let own_start = self.contents.len();
        if !view.roots.is_empty() || template(&name) || nested.is_empty() {
            // Disjoint installation roots are separate units. Overlapping roots
            // remain one explicit choice, so they can never be imported twice.
            let paths: HashSet<_> = view.roots.iter().map(|root| root.path.as_str()).collect();
            let overlap = view.roots.len() > 1
                && (paths.contains("")
                    || view.roots.iter().any(|root| {
                        let mut parent = root.path.as_str();
                        while let Some((next, _)) = parent.rsplit_once('/') {
                            if paths.contains(next) {
                                return true;
                            }
                            parent = next;
                        }
                        false
                    }));
            if !overlap && view.roots.len() > 1 && view.issue.is_none() {
                for root in &view.roots {
                    control.check()?;
                    let unit = ArchiveContent {
                        id: Uuid::new_v4().to_string(),
                        roots: vec![root.clone()],
                        ..view.clone()
                    };
                    self.contents.push(VerifiedContent {
                        view: unit,
                        directory: directory.clone(),
                        inventory: inventory.clone(),
                    });
                }
            } else {
                self.contents.push(VerifiedContent {
                    view,
                    directory: directory.clone(),
                    inventory,
                });
            }
        }
        let own_end = self.contents.len();
        let mut parents = origin;
        parents.push(name);
        for nested in nested {
            let name = nested.rsplit('/').next().unwrap().to_owned();
            self.visit(
                &directory.join(&nested),
                name,
                parents.clone(),
                format!("{source_location} › {nested}"),
                collector,
                depth + 1,
                archives,
                control,
                progress,
            )?;
        }
        let failures: Vec<_> = self.contents[own_end..]
            .iter()
            .filter_map(|item| item.view.issue.clone())
            .collect();
        if !failures.is_empty() {
            let error = AppError::new("templateUnsafeContents").caused_by(failures);
            self.archives.get_mut(&id).unwrap().issue = Some(error.clone());
            for item in &mut self.contents[own_start..own_end] {
                item.view.template_issue = Some(error.clone());
                if item.view.suggested_action == ImportAction::Template {
                    item.view.suggested_action = ImportAction::Exclude;
                }
            }
        }
        Ok(())
    }

    pub fn view(&self) -> ArchiveSessionView {
        ArchiveSessionView {
            id: self.id.clone(),
            contents: self
                .contents
                .iter()
                .map(|content| content.view.clone())
                .collect(),
            template_directory_issue: self.template_directory_issue.clone(),
        }
    }
    pub fn id(&self) -> &str {
        &self.id
    }
    pub fn check_sources(&self, daz_folders: &[String]) -> Result<(), AppError> {
        if self.daz_folders != daz_folders {
            return Err("archiveSourceChanged".into());
        }
        for source in &self.sources {
            if source_stamp(&source.path)? != source.stamp {
                return Err(
                    AppError::new("archiveSourceChanged").param("path", source.path.display())
                );
            }
        }
        Ok(())
    }

    pub(crate) fn template_file(&self, archive_id: &str) -> Result<&VerifiedArchive, AppError> {
        let archive = self
            .archives
            .get(archive_id)
            .ok_or("archiveInvalidTarget")?;
        if let Some(error) = &archive.issue {
            return Err(error.clone());
        }
        if source_stamp(&archive.path)? != archive.stamp {
            return Err("archiveSourceChanged".into());
        }
        Ok(archive)
    }

    pub(crate) fn protects_source(&self, path: &Path) -> Result<bool, AppError> {
        let resolved = path
            .canonicalize()
            .map_err(|error| AppError::diagnostic("ioError", error))?;
        let stamp = source_stamp(path)?;
        for source in &self.sources {
            if source
                .path
                .canonicalize()
                .map_err(|error| AppError::diagnostic("ioError", error))?
                == resolved
                || source.stamp == stamp
            {
                return Ok(true);
            }
        }
        Ok(false)
    }
    pub fn files(&self, id: &str, root_id: &str) -> Result<Vec<(String, PathBuf, u64)>, AppError> {
        let content = self
            .contents
            .iter()
            .find(|content| content.view.id == id && content.view.issue.is_none())
            .ok_or("archiveInvalidTarget")?;
        let root = content
            .view
            .roots
            .iter()
            .find(|root| root.id == root_id)
            .ok_or("archiveInvalidTarget")?;
        let prefix = if root.path.is_empty() {
            String::new()
        } else {
            format!("{}/", root.path)
        };
        let start = content
            .inventory
            .partition_point(|entry| entry.path.as_str() < prefix.as_str());
        Ok(content.inventory[start..]
            .iter()
            .take_while(|entry| entry.path.starts_with(&prefix))
            .filter(|entry| !entry.directory && !archive_name(&entry.path))
            .filter_map(|entry| {
                entry.path.strip_prefix(&prefix).map(|relative| {
                    (
                        relative.to_owned(),
                        content.directory.join(&entry.path),
                        entry.size,
                    )
                })
            })
            .collect())
    }
}
