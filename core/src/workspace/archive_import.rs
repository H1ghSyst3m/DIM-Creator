use super::*;
use crate::{
    archives::{ArchiveControl, ArchiveSession, GIB, ImportAction},
    filesystem::require_space,
    worklist::{Worklist, WorklistEntry, WorklistState},
};

#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ImportAssignment {
    pub content_id: String,
    pub root_id: String,
    pub target_id: String,
    pub action: ImportAction,
}
#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ImportTarget {
    pub id: String,
    pub package_id: Option<String>,
    pub name: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
pub struct ImportGroup {
    pub id: String,
    pub targets: Vec<String>,
}
#[derive(Debug, Clone, Serialize, ts_rs::TS)]
pub struct ImportGroupPreview {
    pub id: String,
    pub targets: Vec<String>,
    pub existing: bool,
    pub members: Vec<Package>,
}
#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
pub struct ImportDraft {
    pub assignments: Vec<ImportAssignment>,
    pub targets: Vec<ImportTarget>,
    pub groups: Vec<ImportGroup>,
}
#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ImportTargetPreview {
    pub id: String,
    pub package: Package,
    pub existing: bool,
    pub sources: Vec<String>,
    pub files: u32,
    pub bytes: u64,
    pub conflicts: Vec<String>,
}
#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ImportPreview {
    pub id: String,
    pub targets: Vec<ImportTargetPreview>,
    pub groups: Vec<ImportGroupPreview>,
    pub skipped: u32,
    pub templates: Vec<TemplatePreview>,
}
#[derive(Debug, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ImportTargetResult {
    pub target_id: String,
    pub package_id: Option<String>,
    pub status: FileStatus,
    pub imported_files: u32,
    pub skipped_files: u32,
    pub error: Option<AppError>,
}
#[derive(Serialize, ts_rs::TS)]
pub struct ImportResult {
    pub results: Vec<ImportTargetResult>,
    pub packages: Vec<Package>,
    pub worklist: Option<WorklistState>,
    pub templates: Vec<TemplateResult>,
}

#[derive(Clone)]
struct Incoming {
    path: String,
    source: PathBuf,
    bytes: u64,
}
struct TargetPlan {
    view: ImportTargetPreview,
    incoming: Vec<Incoming>,
    conflicts: Vec<CopyConflict>,
    original: Option<Package>,
}
pub struct ImportPlan {
    pub view: ImportPreview,
    targets: Vec<TargetPlan>,
    groups: Vec<WorklistEntry>,
    worklist: Option<Worklist>,
    templates: Vec<super::template_import::TemplatePlan>,
    settings: Settings,
    session: String,
    group_members: Vec<Package>,
}

fn has_existing_conflict(
    conflicts: &std::collections::BTreeMap<String, &CopyConflict>,
    path: &str,
) -> bool {
    let key = case_key(path);
    let mut ancestor = key.as_str();
    loop {
        if conflicts.contains_key(ancestor) {
            return true;
        }
        let Some((parent, _)) = ancestor.rsplit_once('/') else {
            break;
        };
        ancestor = parent;
    }
    let prefix = format!("{key}/");
    conflicts
        .range(prefix.clone()..)
        .next()
        .is_some_and(|(path, _)| path.starts_with(&prefix))
}

fn selected_files(incoming: &[Incoming], policy: CopyPolicy) -> (Vec<Incoming>, Vec<String>) {
    let mut files = std::collections::BTreeMap::<String, (usize, Incoming)>::new();
    let mut conflicts = Vec::new();
    for (index, file) in incoming.iter().enumerate() {
        let key = case_key(&file.path);
        let prefix = format!("{key}/");
        let descendant = files
            .range(prefix.clone()..)
            .next()
            .is_some_and(|(path, _)| path.starts_with(&prefix));
        let mut collisions = Vec::new();
        let mut parent = key.as_str();
        loop {
            if files.contains_key(parent) {
                collisions.push(parent.to_owned());
            }
            let Some((next, _)) = parent.rsplit_once('/') else {
                break;
            };
            parent = next;
        }
        if descendant || !collisions.is_empty() {
            conflicts.push(file.path.clone());
            if policy != CopyPolicy::Replace {
                continue;
            }
            collisions.extend(
                files
                    .range(prefix.clone()..)
                    .take_while(|(path, _)| path.starts_with(&prefix))
                    .map(|(path, _)| path.clone()),
            );
            for path in collisions {
                files.remove(&path);
            }
        }
        files.insert(key, (index, file.clone()));
    }
    let mut ordered: Vec<_> = files.into_values().collect();
    ordered.sort_by_key(|(index, _)| *index);
    (
        ordered.into_iter().map(|(_, file)| file).collect(),
        conflicts,
    )
}

fn existing_conflicts(root: &Path, files: &[Incoming]) -> Result<Vec<CopyConflict>, AppError> {
    let mut conflicts = std::collections::BTreeMap::new();
    let mut directories = HashMap::new();
    for file in files {
        let parts: Vec<_> = file.path.split('/').collect();
        let mut parent = root.to_path_buf();
        for (index, part) in parts.iter().enumerate() {
            if !directories.contains_key(&parent) {
                directories.insert(parent.clone(), children(&parent)?);
            }
            let Some(existing) = directories[&parent].get(&case_key(part)).cloned() else {
                break;
            };
            let metadata = regular_item(&existing)?;
            if index + 1 == parts.len() || !metadata.is_dir() {
                let path = copy_relative(&existing, root);
                if let std::collections::btree_map::Entry::Vacant(entry) = conflicts.entry(path) {
                    entry.insert(target_stamp(&existing)?);
                }
                break;
            }
            parent = existing;
        }
    }
    Ok(conflicts
        .into_iter()
        .map(|(path, stamp)| CopyConflict { path, stamp })
        .collect())
}

fn same_conflicts(a: &[CopyConflict], b: &[CopyConflict]) -> bool {
    a.len() == b.len()
        && a.iter()
            .zip(b)
            .all(|(a, b)| a.path == b.path && a.stamp == b.stamp)
}

impl Workspace {
    pub fn plan_import(
        &self,
        archives: &ArchiveSession,
        draft: &ImportDraft,
    ) -> Result<ImportPlan, AppError> {
        let settings = self.settings()?;
        archives.check_sources(&settings.daz_folders)?;
        let available = archives.view();
        let by_id: HashMap<_, _> = available
            .contents
            .iter()
            .map(|item| (item.id.as_str(), item))
            .collect();
        let mut unique = HashSet::new();
        let mut active_archives = HashSet::new();
        for assignment in &draft.assignments {
            if !unique.insert(&assignment.content_id)
                || !by_id.contains_key(assignment.content_id.as_str())
            {
                return Err("archiveInvalidTarget".into());
            }
            if assignment.action != ImportAction::Exclude {
                active_archives.insert(&by_id[assignment.content_id.as_str()].archive_id);
            }
        }
        if unique.len() != available.contents.len() {
            return Err("archiveInvalidTarget".into());
        }
        let mut template_ids = Vec::new();
        for assignment in &draft.assignments {
            if assignment.action != ImportAction::Template {
                continue;
            }
            let content = by_id[assignment.content_id.as_str()];
            if available
                .contents
                .iter()
                .filter(|item| item.archive_id == content.archive_id)
                .any(|item| {
                    !draft.assignments.iter().any(|assignment| {
                        assignment.content_id == item.id
                            && assignment.action == ImportAction::Template
                    })
                })
            {
                return Err("archiveMixedTemplate".into());
            }
            if !template_ids.contains(&content.archive_id) {
                template_ids.push(content.archive_id.clone());
            }
        }
        let included: Vec<_> = draft
            .assignments
            .iter()
            .filter(|item| item.action == ImportAction::Package)
            .collect();
        if included.is_empty() && template_ids.is_empty() {
            return Err("archiveNoSelection".into());
        }
        let templates = self.plan_templates(archives, &template_ids, &settings)?;
        let state = if included.is_empty() {
            None
        } else {
            let state = self.worklist()?;
            if state.save_error.is_some() {
                return Err("worklistSavePending".into());
            }
            let (disk, _) = load_record(&self.root.join("worklist.json"), Worklist::validate)?;
            if disk != state.value {
                return Err("worklistChanged".into());
            }
            Some(state)
        };
        let mut target_ids = HashSet::new();
        let mut existing_ids = HashSet::new();
        let mut targets = Vec::new();
        for target in &draft.targets {
            check_id(&target.id)?;
            if !target_ids.insert(target.id.clone()) {
                return Err("archiveInvalidTarget".into());
            }
            let assignments: Vec<_> = included
                .iter()
                .filter(|item| item.target_id == target.id)
                .collect();
            if assignments.is_empty() {
                continue;
            }
            let original = target
                .package_id
                .as_ref()
                .map(|id| self.package(id))
                .transpose()?;
            let package = if let Some(package) = &original {
                if !existing_ids.insert(package.id.clone()) {
                    return Err("archiveInvalidTarget".into());
                }
                package.clone()
            } else {
                if target.name.trim().is_empty() || target.name.trim().chars().count() > 160 {
                    return Err("invalidProductName".into());
                }
                Package::new(&target.name, &settings)?
            };
            let mut incoming = Vec::new();
            let mut sources = Vec::new();
            for assignment in assignments {
                let content = available
                    .contents
                    .iter()
                    .find(|item| item.id == assignment.content_id)
                    .ok_or("archiveInvalidTarget")?;
                sources.push(content.archive_name.clone());
                incoming.extend(
                    archives
                        .files(&assignment.content_id, &assignment.root_id)?
                        .into_iter()
                        .map(|(path, source, bytes)| Incoming {
                            path,
                            source,
                            bytes,
                        }),
                );
            }
            let (_, mut source_conflicts) = selected_files(&incoming, CopyPolicy::Replace);
            let conflicts = if original.is_some() {
                existing_conflicts(&self.folder(&package.id, "")?, &incoming)?
            } else {
                Vec::new()
            };
            source_conflicts.extend(conflicts.iter().map(|conflict| conflict.path.clone()));
            source_conflicts.sort();
            source_conflicts.dedup();
            let view = ImportTargetPreview {
                id: target.id.clone(),
                package,
                existing: original.is_some(),
                sources,
                files: incoming.len() as u32,
                bytes: incoming.iter().map(|file| file.bytes).sum(),
                conflicts: source_conflicts,
            };
            targets.push(TargetPlan {
                view,
                incoming,
                conflicts,
                original,
            });
        }
        if included.iter().any(|item| {
            !targets
                .iter()
                .any(|target| target.view.id == item.target_id)
        }) {
            return Err("archiveInvalidTarget".into());
        }
        targets.sort_by_key(|target| {
            included
                .iter()
                .position(|item| item.target_id == target.view.id)
        });
        let groups = match &state {
            Some(state) => self.import_groups(&mut targets, &draft.groups, &state.value)?,
            None => Vec::new(),
        };
        let mut group_members = Vec::new();
        for id in groups.iter().flat_map(WorklistEntry::ids) {
            if !targets
                .iter()
                .any(|target| target.original.is_none() && target.view.package.id == *id)
            {
                group_members.push(self.package(id)?);
            }
        }
        let view_groups = groups
            .iter()
            .map(|group| ImportGroupPreview {
                id: group.id().into(),
                existing: state
                    .as_ref()
                    .unwrap()
                    .value
                    .entries
                    .iter()
                    .any(|entry| entry.id() == group.id()),
                members: group
                    .ids()
                    .iter()
                    .map(|id| {
                        targets
                            .iter()
                            .find(|target| target.view.package.id == *id)
                            .map(|target| target.view.package.clone())
                            .unwrap_or_else(|| {
                                group_members
                                    .iter()
                                    .find(|package| package.id == *id)
                                    .unwrap()
                                    .clone()
                            })
                    })
                    .collect(),
                targets: group
                    .ids()
                    .iter()
                    .filter_map(|id| {
                        targets
                            .iter()
                            .find(|target| target.view.package.id == *id)
                            .map(|target| target.view.id.clone())
                    })
                    .collect(),
            })
            .collect();
        Ok(ImportPlan {
            view: ImportPreview {
                id: Uuid::new_v4().to_string(),
                targets: targets.iter().map(|target| target.view.clone()).collect(),
                groups: view_groups,
                skipped: available
                    .contents
                    .iter()
                    .filter(|content| !active_archives.contains(&content.archive_id))
                    .map(|item| &item.archive_id)
                    .collect::<HashSet<_>>()
                    .len() as u32,
                templates: templates.iter().map(|plan| plan.view.clone()).collect(),
            },
            targets,
            groups,
            worklist: state.map(|state| state.value),
            templates,
            settings,
            session: archives.id().into(),
            group_members,
        })
    }

    fn import_groups(
        &self,
        targets: &mut [TargetPlan],
        drafts: &[ImportGroup],
        worklist: &Worklist,
    ) -> Result<Vec<WorklistEntry>, AppError> {
        let mut assigned = HashSet::new();
        let mut ids = HashSet::new();
        let mut groups: Vec<WorklistEntry> = Vec::new();
        for draft in drafts {
            check_id(&draft.id)?;
            if !ids.insert(&draft.id) {
                return Err("archiveInvalidTarget".into());
            }
            let mut members = Vec::new();
            let mut parent: Option<&WorklistEntry> = None;
            for id in &draft.targets {
                if !assigned.insert(id) {
                    return Err("archiveInvalidTarget".into());
                }
                let Some(target) = targets.iter().find(|target| &target.view.id == id) else {
                    continue;
                };
                if target.original.is_some()
                    && let Some(group) = worklist.entries.iter().find(|entry| {
                        matches!(entry, WorklistEntry::Group { .. })
                            && entry.ids().contains(&target.view.package.id)
                    })
                {
                    if parent.is_some_and(|previous| previous.id() != group.id()) {
                        return Err("archiveGroupConflict".into());
                    }
                    parent = Some(group);
                }
                members.push(target.view.package.id.clone());
            }
            if members.is_empty() {
                continue;
            }
            let (id, collapsed) = if let Some(WorklistEntry::Group {
                id,
                members: previous,
                collapsed,
            }) = parent
            {
                let mut combined = previous.clone();
                combined.extend(
                    members
                        .into_iter()
                        .filter(|member| !previous.contains(member)),
                );
                members = combined;
                (id.clone(), *collapsed)
            } else {
                if worklist.entries.iter().any(|entry| entry.id() == draft.id) {
                    return Err("archiveInvalidTarget".into());
                }
                (draft.id.clone(), false)
            };
            if let Some(WorklistEntry::Group {
                members: previous, ..
            }) = groups.iter_mut().find(|group| group.id() == id)
            {
                for member in members {
                    if !previous.contains(&member) {
                        previous.push(member);
                    }
                }
            } else if members.len() > 1 {
                groups.push(WorklistEntry::Group {
                    id,
                    members,
                    collapsed,
                });
            }
        }
        for group in &groups {
            let mut part = 0_u32;
            for member in group.ids() {
                if targets
                    .iter()
                    .any(|target| target.view.package.id == *member && target.original.is_none())
                {
                    continue;
                }
                let package = self.package(member)?;
                if let Ok(number) = package.part.parse::<u32>()
                    && (1..=99).contains(&number)
                {
                    part = part.max(number);
                }
            }
            for member in group.ids() {
                if let Some(target) = targets
                    .iter_mut()
                    .find(|target| target.view.package.id == *member && target.original.is_none())
                {
                    part += 1;
                    if part > 99 {
                        return Err("archivePartLimit".into());
                    }
                    target.view.package.part = format!("{part:02}");
                }
            }
        }
        Ok(groups)
    }

    pub fn apply_import(
        &self,
        archives: &ArchiveSession,
        plan: ImportPlan,
        policy: CopyPolicy,
        control: &ArchiveControl,
        mut progress: impl FnMut(&str),
    ) -> Result<ImportResult, AppError> {
        if archives.id() != plan.session || self.settings()? != plan.settings {
            return Err("archiveSourceChanged".into());
        }
        archives.check_sources(&plan.settings.daz_folders)?;
        for original in &plan.group_members {
            if self.package(&original.id)? != *original {
                return Err("archiveSourceChanged".into());
            }
        }
        let state = if let Some(worklist) = &plan.worklist {
            let state = self.worklist()?;
            let (disk, _) = load_record(&self.root.join("worklist.json"), Worklist::validate)?;
            if state.value != *worklist || disk != *worklist || state.save_error.is_some() {
                return Err("worklistChanged".into());
            }
            Some(state)
        } else {
            None
        };
        let mut result = ImportResult {
            packages: Vec::new(),
            results: Vec::new(),
            worklist: state,
            templates: Vec::new(),
        };
        let mut stopped: Option<AppError> = None;
        for target in plan.targets {
            let id = target.view.id.clone();
            if let Some(error) = stopped.clone().or_else(|| control.check().err()) {
                result.results.push(ImportTargetResult {
                    target_id: id,
                    package_id: None,
                    status: FileStatus::Skipped,
                    imported_files: 0,
                    skipped_files: target.view.files,
                    error: Some(error),
                });
                continue;
            }
            control.set_phase(crate::archives::ArchivePhase::Import);
            progress(&target.view.package.name);
            let operation = self.import_target(&target, policy, control);
            match operation {
                Ok(imported_files) => {
                    result.packages.push(target.view.package.clone());
                    result.results.push(ImportTargetResult {
                        target_id: id,
                        package_id: Some(target.view.package.id.clone()),
                        status: if imported_files == 0 {
                            FileStatus::Skipped
                        } else {
                            FileStatus::Success
                        },
                        imported_files,
                        skipped_files: target.view.files - imported_files,
                        error: None,
                    });
                    result.worklist = Some(self.publish_import_worklist(
                        &plan.groups,
                        &result.packages,
                        plan.worklist.as_ref().unwrap(),
                    )?);
                    stopped = result
                        .worklist
                        .as_ref()
                        .and_then(|state| state.save_error.as_ref())
                        .map(|_| AppError::new("worklistSavePending"));
                }
                Err(error) => {
                    if error.code == "rollbackFailed"
                        && let Some(package) = &target.original
                    {
                        result.packages.push(package.clone());
                    }
                    result.results.push(ImportTargetResult {
                        target_id: id,
                        package_id: target.original.as_ref().map(|package| package.id.clone()),
                        status: if error.code == "rollbackFailed" {
                            FileStatus::Partial
                        } else {
                            FileStatus::Failed
                        },
                        imported_files: 0,
                        skipped_files: 0,
                        error: Some(error),
                    });
                }
            }
        }
        result.templates = self.apply_templates(
            archives,
            &plan.templates,
            policy,
            control,
            stopped,
            &mut progress,
        );
        Ok(result)
    }

    fn import_target(
        &self,
        target: &TargetPlan,
        policy: CopyPolicy,
        control: &ArchiveControl,
    ) -> Result<u32, AppError> {
        control.check()?;
        if target
            .original
            .as_ref()
            .is_some_and(|original| self.package(&original.id).as_ref() != Ok(original))
        {
            return Err("archiveSourceChanged".into());
        }
        if policy == CopyPolicy::Cancel && !target.view.conflicts.is_empty() {
            return Err("copyConflict".into());
        }
        if let Some(original) = &target.original {
            let fresh = existing_conflicts(&self.folder(&original.id, "")?, &target.incoming)?;
            if !same_conflicts(&target.conflicts, &fresh) {
                return Err("destinationChanged".into());
            }
        }
        let (mut files, _) = selected_files(&target.incoming, policy);
        let conflicts: std::collections::BTreeMap<_, _> = target
            .conflicts
            .iter()
            .map(|conflict| (case_key(&conflict.path), conflict))
            .collect();
        if policy == CopyPolicy::Skip {
            files.retain(|file| !has_existing_conflict(&conflicts, &file.path));
        }
        let imported = files.len() as u32;
        let packages = self.root.join("packages");
        require_space(
            &packages,
            target.view.bytes.saturating_mul(2).saturating_add(2 * GIB),
        )?;
        let staging = tempfile::Builder::new()
            .prefix(".import-")
            .tempdir_in(&packages)
            .map_err(io_error)?;
        let content = staging.path().join("Content");
        fs::create_dir(&content).map_err(io_error)?;
        for file in &files {
            control.check()?;
            let path = content.join(&file.path);
            fs::create_dir_all(path.parent().ok_or("archiveUnsafePath")?).map_err(io_error)?;
            check_chain(path.parent().unwrap())?;
            let mut source = open_regular_file(&file.source)?;
            let mut output = File::create_new(&path).map_err(io_error)?;
            let mut buffer = [0_u8; 64 * 1024];
            let mut size = 0_u64;
            loop {
                control.check()?;
                let count = source.read(&mut buffer).map_err(io_error)?;
                if count == 0 {
                    break;
                }
                size += count as u64;
                if size > file.bytes {
                    return Err("archiveSourceChanged".into());
                }
                output.write_all(&buffer[..count]).map_err(io_error)?;
                control.add_processed(count as u64);
            }
            if size != file.bytes {
                return Err("archiveSourceChanged".into());
            }
            output.sync_all().map_err(io_error)?;
        }
        control.check()?;
        if let Some(original) = &target.original {
            let destination = self.folder(&original.id, "")?;
            let sources: Vec<_> = children(&content)?.into_values().collect();
            let operations = copy_plan(&destination, &self.package_dir(&original.id)?, &sources)?;
            let mut prepared = Vec::new();
            for operation in operations {
                if operation.stamp.is_some() && policy == CopyPolicy::Skip {
                    continue;
                }
                if let Some(stamp) = &operation.stamp {
                    let path = copy_relative(&operation.target, &destination);
                    if !conflicts
                        .get(&case_key(&path))
                        .is_some_and(|conflict| conflict.path == path && conflict.stamp == *stamp)
                    {
                        return Err("destinationChanged".into());
                    }
                }
                let source = operation.source.clone();
                prepared.push((operation, source));
            }
            publish_copy(staging, prepared, || {
                control.check()?;
                if self.package(&original.id)? != *original {
                    return Err("archiveSourceChanged".into());
                }
                Ok(())
            })?;
        } else {
            let package = &target.view.package;
            save_record(
                &staging.path().join("package.json"),
                package,
                |value: &Package| value.validate_storage(&package.id),
            )?;
            control.check()?;
            fs::rename(staging.path(), packages.join(&package.id)).map_err(io_error)?;
        }
        Ok(imported)
    }

    fn publish_import_worklist(
        &self,
        groups: &[WorklistEntry],
        successful: &[Package],
        original: &Worklist,
    ) -> Result<WorklistState, AppError> {
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        let mut next = original.clone();
        let mut present: HashSet<_> = original.ids().into_iter().collect();
        for package in successful {
            if present.insert(package.id.clone()) {
                next.entries.push(WorklistEntry::Package {
                    id: package.id.clone(),
                });
            }
        }
        for group in groups {
            let WorklistEntry::Group {
                id,
                members,
                collapsed,
            } = group
            else {
                continue;
            };
            let members: Vec<_> = members
                .iter()
                .filter(|member| present.contains(*member))
                .cloned()
                .collect();
            if members.len() < 2 {
                continue;
            }
            let anchor = next
                .entries
                .iter()
                .position(|entry| entry.id() == id)
                .or_else(|| {
                    next.entries
                        .iter()
                        .position(|entry| entry.ids().iter().any(|member| members.contains(member)))
                })
                .unwrap_or(next.entries.len());
            let index = next
                .entries
                .iter()
                .take(anchor)
                .filter(|entry| {
                    entry.id() != id && !entry.ids().iter().any(|member| members.contains(member))
                })
                .count();
            next.entries.retain(|entry| {
                entry.id() != id && !entry.ids().iter().any(|member| members.contains(member))
            });
            next.entries.insert(
                index.min(next.entries.len()),
                WorklistEntry::Group {
                    id: id.clone(),
                    members,
                    collapsed: *collapsed,
                },
            );
        }
        next.validate()?;
        session.undo = None;
        if next.entries != session.value.entries {
            next.revision = session.value.revision + 1;
            session.save_error =
                save_record(&self.root.join("worklist.json"), &next, Worklist::validate).err();
            session.value = next;
        }
        Ok(session.state())
    }
}

#[cfg(test)]
mod import_index_tests {
    use super::*;
    #[test]
    fn existing_conflict_lookup_preserves_path_boundaries_at_scale() {
        let entries: Vec<_> = (0..20_000)
            .map(|i| CopyConflict {
                path: format!("People/{i:05}.duf"),
                stamp: i.to_string(),
            })
            .collect();
        let conflicts: std::collections::BTreeMap<_, _> = entries
            .iter()
            .map(|entry| (case_key(&entry.path), entry))
            .collect();
        let started = std::time::Instant::now();
        for i in 0..20_000 {
            assert!(has_existing_conflict(
                &conflicts,
                &format!("people/{i:05}.DUF")
            ));
            assert!(!has_existing_conflict(
                &conflicts,
                &format!("Props/{i:05}.duf")
            ));
            if i % 100 == 0 {
                assert!(
                    started.elapsed() < std::time::Duration::from_secs(10),
                    "conflict lookup must not rescan the entire destination inventory per file"
                );
            }
        }
        assert!(has_existing_conflict(&conflicts, "People"));
        assert!(has_existing_conflict(&conflicts, "People/00000.duf/child"));
        assert!(!has_existing_conflict(&conflicts, "People/00000.duf2"));
        assert!(!has_existing_conflict(&conflicts, "People2"));
    }

    #[test]
    fn indexes_large_unique_inventories_without_pairwise_path_comparison() {
        let files: Vec<_> = (0..200_000)
            .map(|i| Incoming {
                path: format!("People/{i}.duf"),
                source: PathBuf::new(),
                bytes: 1,
            })
            .collect();
        let (selected, conflicts) = selected_files(&files, CopyPolicy::Replace);
        assert_eq!(selected.len(), files.len());
        assert!(conflicts.is_empty());
        assert_eq!(selected[12345].path, "People/12345.duf");
    }

    #[test]
    fn skips_repeated_ancestor_conflicts_without_enumerating_all_descendants() {
        let mut files: Vec<_> = (0..20_000)
            .map(|i| Incoming {
                path: format!("People/{i}.duf"),
                source: PathBuf::new(),
                bytes: 1,
            })
            .collect();
        files.extend((0..20_000).map(|_| Incoming {
            path: "People".into(),
            source: PathBuf::new(),
            bytes: 1,
        }));
        let (selected, conflicts) = selected_files(&files, CopyPolicy::Skip);
        assert_eq!(selected.len(), 20_000);
        assert_eq!(conflicts.len(), 20_000);
        assert!(conflicts.iter().all(|path| path == "People"));
    }
}
