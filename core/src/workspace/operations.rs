use super::*;
use crate::error::AppError;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ts_rs::TS)]
#[serde(rename_all = "lowercase")]
pub enum FileChangeKind {
    Copy,
    Move,
    Delete,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ts_rs::TS)]
#[serde(rename_all = "lowercase")]
pub enum FileStatus {
    Success,
    Partial,
    Skipped,
    Failed,
}

#[derive(Clone, Copy, Deserialize, PartialEq, Eq, ts_rs::TS)]
#[serde(rename_all = "lowercase")]
pub enum TransferMode {
    Copy,
    Move,
}

#[derive(Serialize, ts_rs::TS)]
pub struct FileChange {
    pub from: String,
    pub to: Option<String>,
    pub kind: FileChangeKind,
}

#[derive(Serialize, ts_rs::TS)]
pub struct FileResult {
    pub path: String,
    pub status: FileStatus,
    pub message: Option<AppError>,
}

#[derive(Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct FileMutation {
    pub package: Package,
    pub changes: Vec<FileChange>,
    pub results: Vec<FileResult>,
    pub save_error: Option<AppError>,
}

struct Transfer {
    source: PathBuf,
    target: PathBuf,
    stamp: String,
    operations: Vec<CopyOperation>,
}

impl Workspace {
    fn selected_paths(&self, id: &str, paths: &[String]) -> Result<Vec<PathBuf>, AppError> {
        let root = self.folder(id, "")?;
        let mut selected = Vec::new();
        for relative in paths {
            let segments = relative_segments(relative)?;
            if segments.is_empty() {
                return Err("contentRootProtected".into());
            }
            let mut path = root.clone();
            for segment in segments {
                validate_segment(segment)?;
                path = children(&path)?
                    .remove(&case_key(segment))
                    .ok_or("fileUnavailable")?;
                regular_item(&path)?;
            }
            if !selected.contains(&path) {
                selected.push(path);
            }
        }
        selected.sort_by_key(|path| path.components().count());
        let mut roots = Vec::<PathBuf>::new();
        for path in selected {
            if !roots.iter().any(|parent| path_contains(parent, &path)) {
                validate_tree(&path)?;
                roots.push(path);
            }
        }
        Ok(roots)
    }

    fn transfer_plan(
        &self,
        id: &str,
        folder: &str,
        paths: &[String],
        mode: TransferMode,
    ) -> Result<Vec<Transfer>, AppError> {
        let destination = self.folder(id, folder)?;
        let sources = self.selected_paths(id, paths)?;
        let mut names = HashSet::new();
        let existing = children(&destination)?;
        let mut plans = Vec::new();
        for source in &sources {
            if path_contains(source, &destination) {
                return Err("recursiveTransfer".into());
            }
            let name = entry_name(source)?;
            let same_parent = source.parent().is_some_and(|parent| {
                case_key(&parent.to_string_lossy()) == case_key(&destination.to_string_lossy())
            });
            let target = if same_parent && mode == TransferMode::Copy {
                let path = Path::new(name);
                let stem = path
                    .file_stem()
                    .and_then(|value| value.to_str())
                    .unwrap_or(name);
                let extension = if source.is_file() {
                    path.extension()
                        .and_then(|value| value.to_str())
                        .map(|value| format!(".{value}"))
                        .unwrap_or_default()
                } else {
                    String::new()
                };
                let stem = if source.is_dir() { name } else { stem };
                let mut number = 2;
                loop {
                    let name = format!("{stem} ({number}){extension}");
                    validate_segment(&name)?;
                    if !existing.contains_key(&case_key(&name)) && !names.contains(&case_key(&name))
                    {
                        break destination.join(name);
                    }
                    number += 1;
                }
            } else {
                destination.join(name)
            };
            if !names.insert(case_key(entry_name(&target)?)) {
                return Err("duplicateTransferName".into());
            }
            let mut operations = Vec::new();
            if !(same_parent && mode == TransferMode::Move) {
                if sources.iter().any(|other| path_contains(&target, other)) {
                    return Err("recursiveTransfer".into());
                }
                collect_operations(
                    source,
                    target.clone(),
                    existing.get(&case_key(entry_name(&target)?)),
                    &mut operations,
                )?;
            }
            plans.push(Transfer {
                source: source.clone(),
                target,
                stamp: target_stamp(source)?,
                operations,
            });
        }
        Ok(plans)
    }

    pub fn transfer_conflicts(
        &self,
        id: &str,
        folder: &str,
        paths: &[String],
        mode: TransferMode,
    ) -> Result<Vec<CopyConflict>, AppError> {
        let root = self.folder(id, "")?;
        Ok(self
            .transfer_plan(id, folder, paths, mode)?
            .into_iter()
            .flat_map(|plan| plan.operations)
            .filter_map(|operation| {
                operation.stamp.map(|stamp| CopyConflict {
                    path: copy_relative(&operation.target, &root),
                    stamp,
                })
            })
            .collect())
    }

    pub fn transfer_files(
        &self,
        id: &str,
        folder: &str,
        paths: &[String],
        mode: TransferMode,
        policy: CopyPolicy,
        approved: &[CopyConflict],
    ) -> Result<FileMutation, AppError> {
        let root = self.folder(id, "")?;
        let plans = self.transfer_plan(id, folder, paths, mode)?;
        for plan in &plans {
            for operation in &plan.operations {
                if let Some(stamp) = &operation.stamp
                    && policy != CopyPolicy::Skip
                    && (policy != CopyPolicy::Replace
                        || !approved.iter().any(|item| {
                            case_key(&item.path)
                                == case_key(&copy_relative(&operation.target, &root))
                                && &item.stamp == stamp
                        }))
                {
                    return Err("unresolvedFileConflicts".into());
                }
            }
        }
        let mut result = FileMutation {
            package: self.package(id)?,
            changes: Vec::new(),
            results: Vec::new(),
            save_error: None,
        };
        for mut plan in plans {
            let relative = copy_relative(&plan.source, &root);
            let skipped = policy == CopyPolicy::Skip
                && plan
                    .operations
                    .iter()
                    .any(|operation| operation.stamp.is_some());
            let preserved: Vec<_> = plan
                .operations
                .iter()
                .filter(|operation| policy == CopyPolicy::Skip && operation.stamp.is_some())
                .map(|operation| operation.source.clone())
                .collect();
            if policy == CopyPolicy::Skip {
                plan.operations
                    .retain(|operation| operation.stamp.is_none());
            }
            if plan.operations.is_empty()
                && (skipped || mode == TransferMode::Copy || plan.source == plan.target)
            {
                result.results.push(FileResult {
                    path: relative,
                    status: if skipped {
                        FileStatus::Skipped
                    } else {
                        FileStatus::Success
                    },
                    message: None,
                });
                continue;
            }
            let mut next = result.package.clone();
            let mut changes = Vec::new();
            if plan.operations.is_empty() {
                update_exclusions(
                    &mut next.excluded,
                    &result.package.excluded,
                    &relative,
                    &relative,
                    &copy_relative(&plan.target, &root),
                    mode,
                );
            }
            for operation in &plan.operations {
                let from = copy_relative(&operation.source, &root);
                let to = copy_relative(&operation.target, &root);
                update_exclusions(
                    &mut next.excluded,
                    &result.package.excluded,
                    &relative,
                    &from,
                    &to,
                    mode,
                );
                changes.push(FileChange {
                    from,
                    to: Some(to),
                    kind: if mode == TransferMode::Copy {
                        FileChangeKind::Copy
                    } else {
                        FileChangeKind::Move
                    },
                });
            }
            if mode == TransferMode::Move && !skipped {
                next.excluded.retain(|path| !under(path, &relative));
            }
            let outcome = (|| {
                validate_tree(&plan.source)?;
                if target_stamp(&plan.source)? != plan.stamp {
                    return Err("sourceChanged".into());
                }
                let staging = tempfile::Builder::new()
                    .prefix(".transfer-")
                    .tempdir_in(self.package_dir(id)?)
                    .map_err(io_error)?;
                if mode == TransferMode::Copy {
                    let mut prepared = Vec::new();
                    for operation in plan.operations {
                        let staged = staging.path().join(format!("new-{}", prepared.len()));
                        stage_item(&operation.source, &staged)?;
                        prepared.push((operation, staged));
                    }
                    publish_copy(staging, prepared, || self.save_package(&next))
                } else {
                    publish_move(staging, plan.operations, &plan.source, &preserved, || {
                        self.save_package(&next)
                    })
                }
            })();
            match outcome {
                Ok(()) => {
                    if mode == TransferMode::Move && !skipped {
                        changes = vec![FileChange {
                            from: relative.clone(),
                            to: Some(copy_relative(&plan.target, &root)),
                            kind: FileChangeKind::Move,
                        }];
                    }
                    result.package = next;
                    result.changes.extend(changes);
                    result.results.push(FileResult {
                        path: relative,
                        status: if skipped {
                            FileStatus::Partial
                        } else {
                            FileStatus::Success
                        },
                        message: None,
                    });
                }
                Err(message) => result.results.push(FileResult {
                    path: relative,
                    status: FileStatus::Failed,
                    message: Some(message),
                }),
            }
        }
        Ok(result)
    }

    pub fn rename_entry(&self, id: &str, path: &str, name: &str) -> Result<FileMutation, AppError> {
        validate_segment(name)?;
        let root = self.folder(id, "")?;
        let source = self.selected_paths(id, &[path.into()])?.remove(0);
        let target = source.with_file_name(name);
        let mut package = self.package(id)?;
        if source == target {
            return Ok(FileMutation {
                package,
                changes: vec![],
                results: vec![],
                save_error: None,
            });
        }
        let existing = children(source.parent().ok_or("fileUnavailable")?)?;
        if let Some(other) = existing.get(&case_key(name))
            && other != &source
        {
            return Err("fileNameExists".into());
        }
        let from = copy_relative(&source, &root);
        let to = copy_relative(&target, &root);
        let previous = package.excluded.clone();
        update_exclusions(
            &mut package.excluded,
            &previous,
            &from,
            &from,
            &to,
            TransferMode::Move,
        );
        let staging = tempfile::Builder::new()
            .prefix(".rename-")
            .tempdir_in(self.package_dir(id)?)
            .map_err(io_error)?;
        publish_move(
            staging,
            vec![CopyOperation {
                source: source.clone(),
                target,
                stamp: None,
            }],
            &source,
            &[],
            || self.save_package(&package),
        )?;
        Ok(FileMutation {
            package,
            changes: vec![FileChange {
                from,
                to: Some(to),
                kind: FileChangeKind::Move,
            }],
            results: vec![FileResult {
                path: path.into(),
                status: FileStatus::Success,
                message: None,
            }],
            save_error: None,
        })
    }

    pub fn trash_entries(
        &self,
        id: &str,
        paths: &[String],
        mut trash: impl FnMut(&Path) -> Result<(), AppError>,
    ) -> Result<FileMutation, AppError> {
        let root = self.folder(id, "")?;
        let selected = self.selected_paths(id, paths)?;
        let mut result = FileMutation {
            package: self.package(id)?,
            changes: vec![],
            results: vec![],
            save_error: None,
        };
        for path in selected {
            let relative = copy_relative(&path, &root);
            match validate_tree(&path).and_then(|()| trash(&path)) {
                Ok(()) => {
                    result
                        .package
                        .excluded
                        .retain(|entry| !under(entry, &relative));
                    result.changes.push(FileChange {
                        from: relative.clone(),
                        to: None,
                        kind: FileChangeKind::Delete,
                    });
                    result.results.push(FileResult {
                        path: relative,
                        status: FileStatus::Success,
                        message: None,
                    });
                }
                Err(message) => result.results.push(FileResult {
                    path: relative,
                    status: FileStatus::Failed,
                    message: Some(message),
                }),
            }
        }
        if !result.changes.is_empty() {
            result.save_error = self.save_package(&result.package).err();
        }
        Ok(result)
    }
}

fn under(path: &str, parent: &str) -> bool {
    let path = case_key(path);
    let parent = case_key(parent);
    path == parent || path.starts_with(&(parent + "/"))
}

fn update_exclusions(
    next: &mut Vec<String>,
    previous: &[String],
    selected: &str,
    from: &str,
    to: &str,
    mode: TransferMode,
) {
    // Replacement discards exclusions belonging to the old target contents.
    next.retain(|path| !under(path, to) && (mode == TransferMode::Copy || !under(path, from)));
    for excluded in previous {
        let mapped = if under(excluded, from) {
            let suffix = excluded
                .split('/')
                .skip(from.split('/').count())
                .collect::<Vec<_>>()
                .join("/");
            Some(if suffix.is_empty() {
                to.into()
            } else {
                format!("{to}/{suffix}")
            })
        } else if under(from, excluded) && under(excluded, selected) {
            Some(to.into())
        } else {
            None
        };
        if let Some(mapped) = mapped
            && !next.iter().any(|path| case_key(path) == case_key(&mapped))
        {
            next.push(mapped);
        }
    }
}

fn validate_tree(path: &Path) -> Result<(), AppError> {
    if regular_item(path)?.is_dir() {
        for child in children(path)?.into_values() {
            validate_tree(&child)?;
        }
    }
    Ok(())
}

struct Moved {
    source: PathBuf,
    target: PathBuf,
    backup: Option<PathBuf>,
    published: bool,
    staged: PathBuf,
}

fn publish_move(
    staging: TempDir,
    operations: Vec<CopyOperation>,
    source_root: &Path,
    preserved: &[PathBuf],
    finish: impl FnOnce() -> Result<(), AppError>,
) -> Result<(), AppError> {
    let merge = !operations
        .iter()
        .any(|operation| operation.source == source_root);
    let mut journal = Vec::<Moved>::new();
    let mut removed_dirs = Vec::new();
    let outcome = (|| {
        for operation in operations {
            validate_tree(&operation.source)?;
            let parent = operation
                .target
                .parent()
                .ok_or("fileUnavailable")?
                .to_path_buf();
            require_directory(&parent)?;
            let same_entry = case_key(&operation.source.to_string_lossy())
                == case_key(&operation.target.to_string_lossy());
            let current = children(&parent)?.remove(&case_key(entry_name(&operation.target)?));
            let backup = if let Some(stamp) = &operation.stamp {
                let current = current.ok_or("destinationChanged")?;
                if target_stamp(&current)? != *stamp {
                    return Err("destinationChanged".into());
                }
                let backup = staging.path().join(format!("previous-{}", journal.len()));
                fs::rename(current, &backup).map_err(io_error)?;
                Some(backup)
            } else {
                if current.is_some() && !same_entry {
                    return Err("destinationChanged".into());
                }
                None
            };
            let staged = staging.path().join(format!("source-{}", journal.len()));
            journal.push(Moved {
                source: operation.source,
                target: operation.target,
                backup,
                published: false,
                staged,
            });
            let record = journal.last_mut().unwrap();
            fs::rename(&record.source, &record.staged).map_err(io_error)?;
            require_directory(&parent)?;
            if exists(&record.target)? {
                return Err("destinationChanged".into());
            }
            fs::rename(&record.staged, &record.target).map_err(io_error)?;
            record.published = true;
        }
        if merge {
            remove_empty_source_dirs(source_root, preserved, &mut removed_dirs)?;
        }
        finish()
    })();
    if let Err(error) = outcome {
        let mut failures = Vec::new();
        for directory in removed_dirs.iter().rev() {
            if let Err(error) = fs::create_dir(directory) {
                failures.push(io_error(error));
            }
        }
        for record in journal.into_iter().rev() {
            let restore = (|| {
                require_directory(record.source.parent().ok_or("fileUnavailable")?)?;
                let from = if record.published {
                    &record.target
                } else {
                    &record.staged
                };
                if exists(from)? {
                    if record.published
                        && case_key(&record.source.to_string_lossy())
                            == case_key(&record.target.to_string_lossy())
                    {
                        fs::rename(from, &record.staged).map_err(io_error)?;
                        fs::rename(&record.staged, &record.source).map_err(io_error)?;
                    } else {
                        if exists(&record.source)? {
                            return Err("sourceChanged".into());
                        }
                        fs::rename(from, &record.source).map_err(io_error)?;
                    }
                }
                if let Some(backup) = record.backup {
                    if exists(&record.target)? {
                        return Err("destinationChanged".into());
                    }
                    fs::rename(backup, record.target).map_err(io_error)?;
                }
                Ok::<_, AppError>(())
            })();
            if let Err(error) = restore {
                failures.push(error);
            }
        }
        if !failures.is_empty() {
            return Err(AppError::new("rollbackFailed")
                .param("path", staging.keep().display())
                .caused_by(std::iter::once(error).chain(failures)));
        }
        return Err(error);
    }
    Ok(())
}

fn remove_empty_source_dirs(
    path: &Path,
    preserved: &[PathBuf],
    removed: &mut Vec<PathBuf>,
) -> Result<(), AppError> {
    if preserved.iter().any(|keep| path_contains(keep, path))
        || !exists(path)?
        || !regular_item(path)?.is_dir()
    {
        return Ok(());
    }
    for child in children(path)?.into_values() {
        if regular_item(&child)?.is_dir() {
            remove_empty_source_dirs(&child, preserved, removed)?;
        }
    }
    if children(path)?.is_empty() {
        fs::remove_dir(path).map_err(io_error)?;
        removed.push(path.into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn partial_merge_keeps_skipped_sources_and_moves_remaining_exclusions() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temp.path().join("workspace")).unwrap();
        let mut package = workspace.create("Merge").unwrap();
        let root = workspace.folder(&package.id, "").unwrap();
        fs::create_dir_all(root.join("People/Set")).unwrap();
        fs::create_dir_all(root.join("Props/Set")).unwrap();
        fs::write(root.join("People/Set/a.duf"), "source a").unwrap();
        fs::write(root.join("People/Set/b.duf"), "source b").unwrap();
        fs::write(root.join("Props/Set/a.duf"), "target a").unwrap();
        package.excluded.push("People/Set".into());
        workspace.save(&package).unwrap();
        let paths = ["People/Set".into(), "People/Set/a.duf".into()];
        let conflicts = workspace
            .transfer_conflicts(&package.id, "Props", &paths, TransferMode::Move)
            .unwrap();
        assert_eq!(conflicts.len(), 1);
        let partial = workspace
            .transfer_files(
                &package.id,
                "Props",
                &paths,
                TransferMode::Move,
                CopyPolicy::Skip,
                &[],
            )
            .unwrap();
        assert_eq!(partial.results[0].status, FileStatus::Partial);
        assert_eq!(
            fs::read_to_string(root.join("People/Set/a.duf")).unwrap(),
            "source a"
        );
        assert_eq!(
            fs::read_to_string(root.join("Props/Set/a.duf")).unwrap(),
            "target a"
        );
        assert!(partial.package.excluded.contains(&"Props/Set/b.duf".into()));
        fs::write(root.join("Props/Set/a.duf"), "changed after approval").unwrap();
        assert!(
            workspace
                .transfer_files(
                    &package.id,
                    "Props",
                    &paths[..1],
                    TransferMode::Move,
                    CopyPolicy::Replace,
                    &conflicts
                )
                .is_err()
        );
        let conflicts = workspace
            .transfer_conflicts(&package.id, "Props", &paths[..1], TransferMode::Move)
            .unwrap();
        let result = workspace
            .transfer_files(
                &package.id,
                "Props",
                &paths[..1],
                TransferMode::Move,
                CopyPolicy::Replace,
                &conflicts,
            )
            .unwrap();
        assert_eq!(result.results[0].status, FileStatus::Success);
        assert!(!root.join("People/Set").exists());
        assert_eq!(
            fs::read_to_string(root.join("Props/Set/a.duf")).unwrap(),
            "source a"
        );
    }

    #[test]
    fn skipped_empty_folder_survives_partial_move() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temp.path().join("workspace")).unwrap();
        let package = workspace.create("Skip empty").unwrap();
        let root = workspace.folder(&package.id, "").unwrap();
        fs::create_dir_all(root.join("People/Set/Empty")).unwrap();
        fs::create_dir_all(root.join("Props/Set")).unwrap();
        fs::write(root.join("People/Set/fresh.duf"), "fresh").unwrap();
        fs::write(root.join("Props/Set/Empty"), "existing file").unwrap();
        let result = workspace
            .transfer_files(
                &package.id,
                "Props",
                &["People/Set".into()],
                TransferMode::Move,
                CopyPolicy::Skip,
                &[],
            )
            .unwrap();
        assert_eq!(result.results[0].status, FileStatus::Partial);
        assert!(root.join("People/Set/Empty").is_dir());
        assert!(root.join("Props/Set/fresh.duf").is_file());
        assert_eq!(
            fs::read_to_string(root.join("Props/Set/Empty")).unwrap(),
            "existing file"
        );
    }

    #[test]
    fn move_rolls_back_sources_and_replacements_when_metadata_cannot_save() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        let target = temp.path().join("target");
        fs::write(&source, "source").unwrap();
        fs::write(&target, "target").unwrap();
        let staging = tempfile::tempdir_in(temp.path()).unwrap();
        let operation = CopyOperation {
            source: source.clone(),
            target: target.clone(),
            stamp: Some(target_stamp(&target).unwrap()),
        };
        assert!(
            publish_move(staging, vec![operation], &source, &[], || Err(
                "write failed".into()
            ))
            .is_err()
        );
        assert_eq!(fs::read_to_string(source).unwrap(), "source");
        assert_eq!(fs::read_to_string(target).unwrap(), "target");
    }

    #[test]
    fn case_only_rename_rolls_back_when_metadata_save_fails() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("Original.duf");
        fs::write(&source, "original").unwrap();
        let staging = tempfile::tempdir_in(temp.path()).unwrap();
        let operation = CopyOperation {
            source: source.clone(),
            target: temp.path().join("original.duf"),
            stamp: None,
        };
        assert!(
            publish_move(staging, vec![operation], &source, &[], || Err(
                "write failed".into()
            ))
            .is_err()
        );
        let names = fs::read_dir(temp.path())
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        assert_eq!(names, ["Original.duf"]);
    }

    #[test]
    fn copy_rolls_back_replacement_when_metadata_save_fails() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source.duf");
        let target = temp.path().join("target.duf");
        fs::write(&source, "source").unwrap();
        fs::write(&target, "target").unwrap();
        let staging = tempfile::tempdir_in(temp.path()).unwrap();
        let staged = staging.path().join("new");
        stage_item(&source, &staged).unwrap();
        let operation = CopyOperation {
            source: source.clone(),
            target: target.clone(),
            stamp: Some(target_stamp(&target).unwrap()),
        };
        assert!(
            publish_copy(staging, vec![(operation, staged)], || Err(
                "write failed".into()
            ))
            .is_err()
        );
        assert_eq!(fs::read_to_string(source).unwrap(), "source");
        assert_eq!(fs::read_to_string(target).unwrap(), "target");
    }

    #[cfg(windows)]
    #[test]
    fn locked_source_keeps_replacement_and_metadata_unchanged() {
        use std::os::windows::fs::OpenOptionsExt;
        let temp = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temp.path().join("workspace")).unwrap();
        let package = workspace.create("Locked").unwrap();
        let root = workspace.folder(&package.id, "").unwrap();
        fs::create_dir_all(root.join("People")).unwrap();
        fs::create_dir_all(root.join("Props")).unwrap();
        let source = root.join("People/file.duf");
        fs::write(&source, "source").unwrap();
        fs::write(root.join("Props/file.duf"), "target").unwrap();
        let paths = ["People/file.duf".into()];
        let approved = workspace
            .transfer_conflicts(&package.id, "Props", &paths, TransferMode::Move)
            .unwrap();
        let lock = fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&source)
            .unwrap();
        let result = workspace
            .transfer_files(
                &package.id,
                "Props",
                &paths,
                TransferMode::Move,
                CopyPolicy::Replace,
                &approved,
            )
            .unwrap();
        assert_eq!(result.results[0].status, FileStatus::Failed);
        assert!(result.changes.is_empty());
        assert_eq!(result.package, package);
        assert_eq!(
            fs::read_to_string(root.join("Props/file.duf")).unwrap(),
            "target"
        );
        drop(lock);
        assert_eq!(fs::read_to_string(source).unwrap(), "source");
    }

    #[cfg(windows)]
    #[test]
    fn trash_reports_success_and_retryable_metadata_when_save_is_locked() {
        use std::os::windows::fs::OpenOptionsExt;
        let temp = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temp.path().join("workspace")).unwrap();
        let mut package = workspace.create("Trash save failure").unwrap();
        let root = workspace.folder(&package.id, "").unwrap();
        fs::write(root.join("file.duf"), "source").unwrap();
        package.excluded.push("file.duf".into());
        workspace.save(&package).unwrap();
        let metadata = workspace
            .package_dir(&package.id)
            .unwrap()
            .join("package.json");
        let lock = fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(metadata)
            .unwrap();
        let recycled = temp.path().join("recycled.duf");
        let result = workspace
            .trash_entries(&package.id, &["file.duf".into()], |path| {
                fs::rename(path, &recycled).map_err(io_error)
            })
            .unwrap();
        assert_eq!(result.results[0].status, FileStatus::Success);
        assert!(result.save_error.is_some());
        assert!(result.package.excluded.is_empty());
        assert_eq!(
            workspace.package(&package.id).unwrap().excluded,
            ["file.duf"]
        );
        assert!(recycled.is_file());
        assert!(!root.join("file.duf").exists());
        drop(lock);
        workspace.save(&result.package).unwrap();
        assert!(workspace.package(&package.id).unwrap().excluded.is_empty());
    }

    #[test]
    fn empty_directory_merge_removes_source_and_case_rename_preserves_empty_target() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temp.path().join("workspace")).unwrap();
        let package = workspace.create("Empty").unwrap();
        let root = workspace.folder(&package.id, "").unwrap();
        fs::create_dir_all(root.join("People/Empty")).unwrap();
        fs::create_dir_all(root.join("Props/Empty")).unwrap();
        let result = workspace
            .transfer_files(
                &package.id,
                "Props",
                &["People/Empty".into()],
                TransferMode::Move,
                CopyPolicy::Cancel,
                &[],
            )
            .unwrap();
        assert_eq!(result.results[0].status, FileStatus::Success);
        assert!(!root.join("People/Empty").exists());
        workspace
            .rename_entry(&package.id, "Props/Empty", "empty")
            .unwrap();
        assert!(root.join("Props/empty").is_dir());
    }

    #[test]
    fn trash_failure_keeps_files_and_exclusions_and_root_is_protected() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temp.path().join("workspace")).unwrap();
        let mut package = workspace.create("Trash").unwrap();
        let root = workspace.folder(&package.id, "").unwrap();
        fs::write(root.join("file.duf"), "original").unwrap();
        package.excluded.push("file.duf".into());
        workspace.save(&package).unwrap();
        let result = workspace
            .trash_entries(&package.id, &["file.duf".into()], |_| Err("locked".into()))
            .unwrap();
        assert_eq!(result.results[0].status, FileStatus::Failed);
        assert_eq!(result.package, package);
        assert!(root.join("file.duf").is_file());
        for path in ["", "../package.json", "C:/outside"] {
            assert!(
                workspace
                    .trash_entries(&package.id, &[path.into()], |_| panic!(
                        "must not reach trash"
                    ))
                    .is_err()
            );
        }
    }
}
