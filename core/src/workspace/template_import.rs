use super::*;
use crate::{
    archives::{ArchiveControl, ArchivePhase, ArchiveSession, GIB, template_directory},
    filesystem::{open_directory, require_space},
};

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct TemplatePreview {
    pub archive_id: String,
    pub name: String,
    pub origin: Vec<String>,
    pub source_location: String,
    pub path: PathBuf,
    pub bytes: u64,
    pub conflicts: Vec<String>,
    pub existing_path: Option<PathBuf>,
    pub conflicting_archives: Vec<String>,
    pub save_on_replace: bool,
    pub save_on_skip: bool,
}

#[derive(Debug, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub enum TemplateSkipReason {
    Existing,
    Collision,
}

#[derive(Debug, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct TemplateResult {
    pub archive_id: String,
    pub path: PathBuf,
    pub status: FileStatus,
    pub reason: Option<TemplateSkipReason>,
    pub error: Option<AppError>,
}

pub(super) struct TemplatePlan {
    pub view: TemplatePreview,
    directory: PathBuf,
    directory_stamp: u64,
    existing: Option<(PathBuf, String)>,
}

fn directory_stamp(file: &File) -> Result<u64, AppError> {
    let mut hash = DefaultHasher::new();
    file.metadata()
        .map_err(io_error)?
        .created()
        .ok()
        .hash(&mut hash);
    hash_file_identity(file, &mut hash).map_err(io_error)?;
    Ok(hash.finish())
}

fn existing_file(directory: &Path, name: &str) -> Result<Option<(PathBuf, String)>, AppError> {
    let Some(path) = children(directory)?.remove(&case_key(name)) else {
        return Ok(None);
    };
    if !regular_item(&path)?.is_file() {
        return Err(AppError::new("templateDestinationType").param("path", path.display()));
    }
    let stamp = target_stamp(&path)?;
    Ok(Some((path, stamp)))
}

impl Workspace {
    pub(super) fn plan_templates(
        &self,
        archives: &ArchiveSession,
        ids: &[String],
        settings: &Settings,
    ) -> Result<Vec<TemplatePlan>, AppError> {
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let path = Path::new(
            settings
                .template_dir
                .as_deref()
                .ok_or("templateDirectoryRequired")?,
        );
        let resolved = template_directory(path, &self.root)?;
        let guard = open_directory(path)?;
        let stamp = directory_stamp(&guard)?;
        let session = archives.view();
        let mut plans = Vec::new();
        for id in ids {
            let archive = archives.template_file(id)?;
            validate_segment(&archive.name)?;
            let existing = existing_file(path, &archive.name)?;
            if let Some((existing, _)) = &existing
                && archives.protects_source(existing)?
            {
                return Err(
                    AppError::new("templateReplacesSource").param("path", existing.display())
                );
            }
            let content = session
                .contents
                .iter()
                .find(|content| content.archive_id == *id)
                .ok_or("archiveInvalidTarget")?;
            plans.push(TemplatePlan {
                view: TemplatePreview {
                    archive_id: id.clone(),
                    name: archive.name.clone(),
                    origin: content.origin.clone(),
                    source_location: archive.source_location.clone(),
                    path: path.join(&archive.name),
                    bytes: archive.bytes,
                    conflicts: Vec::new(),
                    existing_path: existing.as_ref().map(|(path, _)| path.clone()),
                    conflicting_archives: Vec::new(),
                    save_on_replace: true,
                    save_on_skip: existing.is_none(),
                },
                directory: resolved.clone(),
                directory_stamp: stamp,
                existing,
            });
        }
        let mut collisions: HashMap<String, Vec<usize>> = HashMap::new();
        for (index, plan) in plans.iter().enumerate() {
            collisions
                .entry(case_key(&plan.view.name))
                .or_default()
                .push(index);
        }
        for indices in collisions.values() {
            for &index in indices {
                let conflicting_archives = indices
                    .iter()
                    .filter(|&&other| other != index)
                    .map(|&other| plans[other].view.archive_id.clone())
                    .collect();
                let plan = &mut plans[index];
                plan.view.conflicting_archives = conflicting_archives;
                if plan.existing.is_some() || indices.len() > 1 {
                    plan.view
                        .conflicts
                        .push(plan.view.path.to_string_lossy().into_owned());
                }
                plan.view.save_on_replace = indices.last() == Some(&index);
                plan.view.save_on_skip &= indices.first() == Some(&index);
            }
        }
        Ok(plans)
    }

    pub(super) fn apply_templates(
        &self,
        archives: &ArchiveSession,
        plans: &[TemplatePlan],
        policy: CopyPolicy,
        control: &ArchiveControl,
        stopped: Option<AppError>,
        progress: &mut impl FnMut(&str),
    ) -> Vec<TemplateResult> {
        // Validate skipped sources too, before this job changes any shared target.
        let checked: Vec<_> = plans
            .iter()
            .map(|plan| {
                control.check()?;
                self.check_template(archives, plan)
            })
            .collect();
        plans
            .iter()
            .zip(checked)
            .map(|(plan, checked)| {
                let mut result = TemplateResult {
                    archive_id: plan.view.archive_id.clone(),
                    path: plan.view.path.clone(),
                    status: FileStatus::Skipped,
                    reason: None,
                    error: None,
                };
                if let Some(error) = stopped.clone().or_else(|| control.check().err()) {
                    result.error = Some(error);
                    return result;
                }
                let _guard = match checked {
                    Ok(guard) => guard,
                    Err(error) => {
                        result.status = FileStatus::Failed;
                        result.error = Some(error);
                        return result;
                    }
                };
                if policy == CopyPolicy::Cancel && !plan.view.conflicts.is_empty() {
                    result.status = FileStatus::Failed;
                    result.error = Some("copyConflict".into());
                    return result;
                }
                let selected = match policy {
                    CopyPolicy::Replace => plan.view.save_on_replace,
                    CopyPolicy::Skip => plan.view.save_on_skip,
                    CopyPolicy::Cancel => true,
                };
                if !selected {
                    result.reason =
                        Some(if policy == CopyPolicy::Skip && plan.existing.is_some() {
                            TemplateSkipReason::Existing
                        } else {
                            TemplateSkipReason::Collision
                        });
                    return result;
                }
                control.set_phase(ArchivePhase::Template);
                progress(&plan.view.name);
                match self.copy_template(archives, plan, control) {
                    Ok(()) => result.status = FileStatus::Success,
                    Err(error) => {
                        result.status = if error.code == "rollbackFailed" {
                            FileStatus::Partial
                        } else {
                            FileStatus::Failed
                        };
                        result.error = Some(error);
                    }
                }
                result
            })
            .collect()
    }

    fn check_template(
        &self,
        archives: &ArchiveSession,
        plan: &TemplatePlan,
    ) -> Result<File, AppError> {
        let parent = plan.view.path.parent().ok_or("templateDirectoryInvalid")?;
        let guard = open_directory(parent)?;
        if template_directory(parent, &self.root)? != plan.directory
            || directory_stamp(&guard)? != plan.directory_stamp
            || existing_file(parent, &plan.view.name)? != plan.existing
        {
            return Err("destinationChanged".into());
        }
        archives.template_file(&plan.view.archive_id)?;
        Ok(guard)
    }

    fn copy_template(
        &self,
        archives: &ArchiveSession,
        plan: &TemplatePlan,
        control: &ArchiveControl,
    ) -> Result<(), AppError> {
        let parent = plan.view.path.parent().ok_or("templateDirectoryInvalid")?;
        let _guard = self.check_template(archives, plan)?;
        let check = || -> Result<(), AppError> {
            control.check()?;
            self.check_template(archives, plan)?;
            Ok(())
        };
        check()?;
        let archive = archives.template_file(&plan.view.archive_id)?;
        let mut source = open_regular_file(&archive.path)?;
        let backup_bytes = plan
            .existing
            .as_ref()
            .map(|(path, _)| fs::metadata(path).map(|meta| meta.len()))
            .transpose()
            .map_err(io_error)?
            .unwrap_or(0);
        require_space(
            parent,
            archive
                .bytes
                .saturating_add(backup_bytes)
                .saturating_add(2 * GIB),
        )?;
        let staging = tempfile::Builder::new()
            .prefix(".dim-template-")
            .tempdir_in(parent)
            .map_err(io_error)?;
        let staged = staging.path().join("archive");
        let mut output = File::create_new(&staged).map_err(io_error)?;
        let mut copied = 0_u64;
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            control.check()?;
            let count = source.read(&mut buffer).map_err(io_error)?;
            if count == 0 {
                break;
            }
            copied += count as u64;
            if copied > archive.bytes {
                return Err("archiveSourceChanged".into());
            }
            output.write_all(&buffer[..count]).map_err(io_error)?;
            control.add_processed(count as u64);
        }
        if copied != archive.bytes {
            return Err("archiveSourceChanged".into());
        }
        output.sync_all().map_err(io_error)?;
        drop(output);
        check()?;
        let operation = CopyOperation {
            source: archive.path.clone(),
            target: plan.view.path.clone(),
            stamp: plan.existing.as_ref().map(|(_, stamp)| stamp.clone()),
        };
        publish_copy(staging, vec![(operation, staged)], || {
            control.check()?;
            archives.template_file(&plan.view.archive_id)?;
            if template_directory(parent, &self.root)? != plan.directory {
                return Err("destinationChanged".into());
            }
            Ok(())
        })
    }
}
