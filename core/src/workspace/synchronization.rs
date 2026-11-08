use super::*;
use crate::{
    synchronization::*,
    worklist::{Worklist, WorklistEntry, WorklistState},
};
use std::collections::BTreeMap;

pub struct SyncPlan {
    pub changes: Vec<SyncChange>,
    pub order: Option<Vec<String>>,
    pub stamp: String,
    request: SyncRequest,
    packages: Vec<Package>,
    covers: BTreeMap<String, Option<Vec<u8>>>,
}

impl SyncPlan {
    pub fn packages(&self) -> Vec<Package> {
        self.packages
            .iter()
            .map(|package| {
                self.changes
                    .iter()
                    .find(|change| change.after.id == package.id)
                    .map_or_else(|| package.clone(), |change| change.after.clone())
            })
            .collect()
    }
}

#[derive(Debug, Serialize, ts_rs::TS)]
pub struct SyncFailure {
    pub id: String,
    pub error: AppError,
}

#[derive(Debug, Serialize, ts_rs::TS)]
pub struct SyncResult {
    pub packages: Vec<Package>,
    pub unavailable: Vec<SyncFailure>,
    pub worklist: WorklistState,
    pub error: Option<AppError>,
}

pub fn fingerprint(value: &impl Serialize) -> Result<String, AppError> {
    let mut hasher = DefaultHasher::new();
    serde_json::to_vec(value)
        .map_err(|e| AppError::diagnostic("operationError", e))?
        .hash(&mut hasher);
    Ok(format!("{:016x}", hasher.finish()))
}

fn read_optional(path: &Path) -> Result<Option<Vec<u8>>, AppError> {
    if !exists(path)? {
        return Ok(None);
    }
    let mut bytes = Vec::new();
    open_replaceable_file(path)?
        .read_to_end(&mut bytes)
        .map_err(io_error)?;
    Ok(Some(bytes))
}

fn record_stamp(path: &Path) -> Result<String, AppError> {
    fingerprint(&(read_optional(path)?, read_optional(&backup_path(path))?))
}

#[cfg(test)]
type TestHook = Box<dyn FnMut(&str) -> Result<(), AppError>>;
#[cfg(test)]
thread_local! { static FAULT: std::cell::RefCell<Option<TestHook>> = const { std::cell::RefCell::new(None) }; }
#[cfg(test)]
fn checkpoint(point: &str) -> Result<(), AppError> {
    FAULT.with_borrow_mut(|hook| hook.as_mut().map_or(Ok(()), |hook| hook(point)))
}

impl Workspace {
    pub fn load_sync(&self, group: &str) -> Result<SyncGroup, AppError> {
        let state = self.worklist()?;
        if state.save_error.is_some() {
            return Err("worklistSavePending".into());
        }
        let path = self.root.join("worklist.json");
        let (disk, _) = load_record(&path, Worklist::validate)?;
        if disk != state.value {
            return Err("syncStale".into());
        }
        let members = match state.value.entries.iter().find(|entry| entry.id() == group) {
            Some(WorklistEntry::Group { members, .. }) => members,
            _ => return Err("invalidSyncAction".into()),
        };
        let mut packages = Vec::new();
        let mut stamps = BTreeMap::new();
        for id in members {
            let package = self.package(id).map_err(|error| {
                AppError::new("packageUnavailable")
                    .param("id", id)
                    .caused_by([error])
            })?;
            stamps.insert(
                id.clone(),
                record_stamp(&self.package_dir(id)?.join("package.json"))?,
            );
            packages.push(package);
        }
        Ok(SyncGroup {
            group: group.into(),
            packages,
            expected: SyncExpected {
                revision: state.value.revision,
                worklist_stamp: record_stamp(&path)?,
                packages: stamps,
            },
        })
    }

    pub fn plan_sync(&self, request: &SyncRequest) -> Result<SyncPlan, AppError> {
        let loaded = self.load_sync(&request.group)?;
        if fingerprint(&loaded.expected)? != fingerprint(&request.expected)? {
            return Err("syncStale".into());
        }
        let mut changes = plan_changes(
            &loaded.packages,
            &request.source,
            &request.targets,
            &request.fields,
            request.order.as_deref(),
        )?;
        let mut covers = BTreeMap::new();
        if request.fields.contains(&SyncField::Cover) && !request.targets.is_empty() {
            for id in std::iter::once(&request.source).chain(request.targets.iter()) {
                let package = loaded
                    .packages
                    .iter()
                    .find(|p| &p.id == id)
                    .ok_or("invalidSyncAction")?;
                let clearing = id != &request.source
                    && covers.get(&request.source).is_some_and(Option::is_none);
                let bytes = package
                    .cover
                    .as_ref()
                    .filter(|_| !clearing)
                    .map(|cover| {
                        crate::package::managed_cover(&self.package_dir(id)?, cover)
                            .map(|(bytes, _)| bytes)
                    })
                    .transpose()?;
                covers.insert(id.clone(), bytes);
            }
            for id in &request.targets {
                let before = loaded.packages.iter().find(|p| &p.id == id).unwrap();
                let same = if covers[&request.source].is_none() {
                    before.cover.is_none()
                } else {
                    covers[id] == covers[&request.source]
                };
                if same {
                    continue;
                }
                let mut after = changes
                    .iter()
                    .find(|c| &c.after.id == id)
                    .map_or_else(|| before.clone(), |c| c.after.clone());
                after.cover = covers[&request.source]
                    .as_ref()
                    .map(|_| format!("cover-{}.jpg", Uuid::new_v4()));
                changes.retain(|c| &c.after.id != id);
                changes.push(SyncChange::between(before, after, true));
            }
        }
        changes.sort_by_key(|c| loaded.packages.iter().position(|p| p.id == c.after.id));
        let current: Vec<_> = loaded.packages.iter().map(|p| p.id.clone()).collect();
        let order = request
            .order
            .as_ref()
            .filter(|order| **order != current)
            .cloned();
        let stamp = fingerprint(&(request, &covers))?;
        Ok(SyncPlan {
            changes,
            order,
            stamp,
            request: request.clone(),
            packages: loaded.packages,
            covers,
        })
    }

    pub fn apply_sync_plan(&self, plan: SyncPlan) -> Result<SyncResult, AppError> {
        if self.plan_sync(&plan.request)?.stamp != plan.stamp {
            return Err("syncStale".into());
        }
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        if plan.changes.is_empty() && plan.order.is_none() {
            return Ok(SyncResult {
                packages: plan.packages,
                unavailable: vec![],
                worklist: session.state(),
                error: None,
            });
        }
        let previous = session.value.clone();
        let mut next = previous.clone();
        if let Some(order) = &plan.order {
            let Some(WorklistEntry::Group { members, .. }) = next
                .entries
                .iter_mut()
                .find(|entry| entry.id() == plan.request.group)
            else {
                return Err("syncStale".into());
            };
            members.clone_from(order);
            next.revision += 1;
            next.validate()?;
        }
        // Preserve exact primary/backup bytes, including missing backups. Old covers are
        // not collected during a multi-record operation: a rollback may still need them.
        let recovery = tempfile::Builder::new()
            .prefix(".sync-recovery-")
            .tempdir_in(&self.root)
            .map_err(io_error)?;
        let mut records = Vec::new();
        for change in &plan.changes {
            change.after.validate_storage(&change.before.id)?;
            let path = self.package_dir(&change.before.id)?.join("package.json");
            for file in [&path, &backup_path(&path)] {
                if let Err(RecordError::Protected(error)) =
                    read_record(file, &|p: &Package| p.validate_storage(&change.before.id))
                {
                    return Err(error);
                }
            }
            records.push(path);
        }
        if plan.order.is_some() {
            let path = self.root.join("worklist.json");
            for file in [&path, &backup_path(&path)] {
                if let Err(RecordError::Protected(error)) = read_record(file, &Worklist::validate) {
                    return Err(error);
                }
            }
            records.push(path);
        }
        let mut snapshots = Vec::new();
        for path in &records {
            for file in [path.clone(), backup_path(path)] {
                let bytes = read_optional(&file)?;
                let saved = recovery.path().join(format!("{}.json", snapshots.len()));
                if let Some(bytes) = &bytes {
                    atomic_write(&saved, bytes)?;
                }
                snapshots.push((file, bytes, saved));
            }
        }
        // Keep a concrete recovery map with the staged records, only retained on failure.
        atomic_write(
            &recovery.path().join("records.json"),
            &serde_json::to_vec(
                &snapshots
                    .iter()
                    .map(|(path, bytes, saved)| (path, bytes.is_some(), saved))
                    .collect::<Vec<_>>(),
            )
            .map_err(|e| AppError::diagnostic("operationError", e))?,
        )?;
        let mut new_covers = Vec::new();
        let mut touched = HashSet::new();
        let mut written = HashMap::new();
        let write = (|| {
            for change in &plan.changes {
                if change.fields.contains(&SyncField::Cover)
                    && let Some(name) = &change.after.cover
                {
                    let path = self.package_dir(&change.after.id)?.join(name);
                    let mut temporary =
                        NamedTempFile::new_in(path.parent().unwrap()).map_err(io_error)?;
                    temporary
                        .write_all(plan.covers[&plan.request.source].as_ref().unwrap())
                        .map_err(io_error)?;
                    temporary.as_file().sync_all().map_err(io_error)?;
                    temporary
                        .persist_noclobber(&path)
                        .map_err(|e| io_error(e.error))?;
                    new_covers.push(path);
                }
            }
            #[cfg(test)]
            checkpoint("prepared")?;
            if record_stamp(&self.root.join("worklist.json"))?
                != plan.request.expected.worklist_stamp
            {
                return Err("syncStale".into());
            }
            for package in &plan.packages {
                if record_stamp(&self.package_dir(&package.id)?.join("package.json"))?
                    != plan.request.expected.packages[&package.id]
                {
                    return Err("syncStale".into());
                }
                if let Some(expected) = plan.covers.get(&package.id) {
                    // Clearing a reference never needs the old image; otherwise compare the
                    // bytes again after staging, while still holding the desktop write lock.
                    if plan.covers[&plan.request.source].is_none() {
                        continue;
                    }
                    let actual = package
                        .cover
                        .as_ref()
                        .map(|cover| {
                            crate::package::managed_cover(&self.package_dir(&package.id)?, cover)
                                .map(|(bytes, _)| bytes)
                        })
                        .transpose()?;
                    if &actual != expected {
                        return Err("syncStale".into());
                    }
                }
            }
            for change in &plan.changes {
                #[cfg(test)]
                checkpoint("packageWrite")?;
                let path = self.package_dir(&change.after.id)?.join("package.json");
                if record_stamp(&path)? != plan.request.expected.packages[&change.after.id] {
                    return Err("syncStale".into());
                }
                written.insert(path.clone(), Some(record_bytes(&change.after)?));
                written.insert(backup_path(&path), read_optional(&path)?);
                touched.insert(path.clone());
                save_record(&path, &change.after, |p: &Package| {
                    p.validate_storage(&change.after.id)
                })?;
            }
            if plan.order.is_some() {
                let path = self.root.join("worklist.json");
                if record_stamp(&path)? != plan.request.expected.worklist_stamp {
                    return Err("syncStale".into());
                }
                written.insert(path.clone(), Some(record_bytes(&next)?));
                written.insert(backup_path(&path), read_optional(&path)?);
                touched.insert(path.clone());
                save_record(&path, &next, Worklist::validate)?;
            }
            Ok::<_, AppError>(())
        })();
        let mut error = if let Err(error) = write {
            let mut failures = Vec::new();
            for (path, bytes, _) in snapshots.iter().rev().filter(|(path, _, _)| {
                touched
                    .iter()
                    .any(|record| path == record || *path == backup_path(record))
            }) {
                match read_optional(path) {
                    Ok(current) if current == *bytes => continue,
                    Ok(current) if written.get(path) == Some(&current) => {}
                    // Do not roll an external edit or a newly protected record back to our snapshot.
                    Ok(_) => {
                        failures.push(AppError::new("syncStale").param("path", path.display()));
                        continue;
                    }
                    Err(error) => {
                        failures.push(error);
                        continue;
                    }
                }
                let restore = || {
                    #[cfg(test)]
                    checkpoint("rollback")?;
                    match bytes {
                        Some(bytes) => atomic_write(path, bytes),
                        None => exists(path).and_then(|present| {
                            if present {
                                check_chain(path)
                                    .and_then(|()| fs::remove_file(path).map_err(io_error))
                            } else {
                                Ok(())
                            }
                        }),
                    }
                };
                let restored = restore();
                if let Err(error) = restored {
                    failures.push(error);
                }
            }
            if failures.is_empty() {
                for path in new_covers {
                    let _ = fs::remove_file(path);
                }
                return Err(error);
            }
            let path = recovery.keep();
            Some(
                AppError::new("rollbackFailed")
                    .param("path", path.display())
                    .caused_by(std::iter::once(error).chain(failures)),
            )
        } else {
            None
        };
        if error.is_none() {
            session.value = next;
        } else {
            let recover = || {
                #[cfg(test)]
                checkpoint("worklistRead")?;
                load_record(&self.root.join("worklist.json"), Worklist::validate)
            };
            let recovered = recover();
            match recovered {
                Ok((actual, _)) => session.value = actual,
                Err(error) => {
                    session.recovery_error =
                        Some(AppError::new("syncRecoveryRequired").caused_by([error]))
                }
            }
        }
        let mut packages = Vec::new();
        let mut unavailable = Vec::new();
        for original in &plan.packages {
            let read = || {
                #[cfg(test)]
                checkpoint("packageRead")?;
                self.package(&original.id)
            };
            let readback = read();
            match readback {
                Ok(package) => packages.push(package),
                Err(error) => unavailable.push(SyncFailure {
                    id: original.id.clone(),
                    error,
                }),
            }
        }
        if !unavailable.is_empty() {
            let readback =
                AppError::new("syncReadbackFailed").caused_by(unavailable.iter().map(|failure| {
                    AppError::new("packageUnavailable")
                        .param("id", &failure.id)
                        .caused_by([failure.error.clone()])
                }));
            error = Some(match error {
                Some(error) => error.caused_by([readback]),
                None => readback,
            });
        }
        if error.is_none()
            || session.recovery_error.is_some()
            || !unavailable.is_empty()
            || packages != plan.packages
            || session.value != previous
        {
            session.undo = None;
        }
        Ok(SyncResult {
            packages,
            unavailable,
            worklist: session.state(),
            error,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::worklist::WorklistAction;
    struct Fault;
    impl Drop for Fault {
        fn drop(&mut self) {
            FAULT.with_borrow_mut(|hook| *hook = None);
        }
    }
    fn inject(hook: impl FnMut(&str) -> Result<(), AppError> + 'static) -> Fault {
        FAULT.with_borrow_mut(|slot| *slot = Some(Box::new(hook)));
        Fault
    }
    fn fixture() -> (TempDir, Workspace, SyncRequest) {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::new(dir.path().to_owned()).unwrap();
        let ids: Vec<_> = (0..3)
            .map(|n| {
                ws.create_listed(&format!("Package {n}"))
                    .unwrap()
                    .package
                    .id
            })
            .collect();
        let state = ws
            .change_worklist(
                ws.worklist().unwrap().value.revision,
                &WorklistAction::Group { ids: ids.clone() },
            )
            .unwrap();
        let group = state.value.entries[0].id().to_owned();
        let expected = ws.load_sync(&group).unwrap().expected;
        (
            dir,
            ws,
            SyncRequest {
                group,
                source: ids[0].clone(),
                targets: ids[1..].to_vec(),
                fields: vec![SyncField::Name],
                order: None,
                expected,
            },
        )
    }

    #[test]
    fn unreadable_readback_is_not_reported_as_success() {
        let (_dir, ws, request) = fixture();
        let _fault = inject(|point| {
            if point == "packageRead" {
                Err("ioError".into())
            } else {
                Ok(())
            }
        });
        let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
        assert_eq!(result.error.unwrap().code, "syncReadbackFailed");
        assert_eq!(result.unavailable.len(), 3);
        assert!(result.packages.is_empty());
        assert!(!result.worklist.can_undo);
    }

    #[test]
    fn incomplete_rollback_preserves_recovery_files_and_blocks_unreadable_structure() {
        let (_dir, ws, request) = fixture();
        let mut writes = 0;
        let fault = inject(move |point| {
            if point == "packageWrite" {
                writes += 1;
                if writes == 2 {
                    return Err("ioError".into());
                }
            }
            if ["rollback", "worklistRead"].contains(&point) {
                return Err("ioError".into());
            }
            Ok(())
        });
        let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
        let error = result.error.unwrap();
        assert_eq!(error.code, "rollbackFailed");
        assert!(
            Path::new(&error.params["path"])
                .join("records.json")
                .exists()
        );
        assert!(
            result
                .packages
                .iter()
                .any(|p| p.id == request.targets[0] && p.name == "Package 0")
        );
        assert!(!result.worklist.can_undo);
        assert!(result.worklist.save_error.is_none());
        drop(fault);
        assert_eq!(
            ws.retry_worklist().unwrap_err().code,
            "syncRecoveryRequired"
        );
        assert!(ws.create_listed("Blocked").is_err());
        assert!(ws.undo_worklist(result.worklist.value.revision).is_err());
        ws.reload_sync_worklist().unwrap();
        assert!(ws.load_sync(&request.group).is_ok());
    }

    #[test]
    fn rollback_does_not_overwrite_an_external_newer_record() {
        let (_dir, ws, request) = fixture();
        let path = ws
            .package_dir(&request.targets[0])
            .unwrap()
            .join("package.json");
        let mut record: serde_json::Value =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        record["version"] = serde_json::json!(999);
        let protected = serde_json::to_vec(&record).unwrap();
        let expected = protected.clone();
        let changed = path.clone();
        let mut writes = 0;
        let _fault = inject(move |point| {
            if point == "packageWrite" {
                writes += 1;
                if writes == 2 {
                    atomic_write(&changed, &protected)?;
                    return Err("ioError".into());
                }
            }
            Ok(())
        });
        let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
        assert_eq!(result.error.unwrap().code, "rollbackFailed");
        assert_eq!(fs::read(path).unwrap(), expected);
        assert!(
            result
                .unavailable
                .iter()
                .any(|entry| entry.id == request.targets[0])
        );
        assert!(!result.worklist.can_undo);
    }

    #[test]
    fn reload_adopts_disk_order_and_rejects_changes_during_preparation() {
        let (dir, ws, request) = fixture();
        let mut external = ws.package(&request.targets[1]).unwrap();
        external.name = "External edit".into();
        let path = ws.package_dir(&external.id).unwrap().join("package.json");
        let fault = inject(move |point| {
            if point == "prepared" {
                atomic_write(&path, &record_bytes(&external)?)?;
            }
            Ok(())
        });
        assert_eq!(
            ws.apply_sync_plan(ws.plan_sync(&request).unwrap())
                .unwrap_err()
                .code,
            "syncStale"
        );
        assert_eq!(
            ws.package(&request.targets[1]).unwrap().name,
            "External edit"
        );
        drop(fault);
        let mut state = ws.worklist().unwrap().value;
        if let WorklistEntry::Group { members, .. } = &mut state.entries[0] {
            members.reverse();
        }
        state.revision = 0;
        atomic_write(
            &dir.path().join("worklist.json"),
            &record_bytes(&state).unwrap(),
        )
        .unwrap();
        assert!(ws.load_sync(&request.group).is_err());
        let reloaded = ws.reload_sync_worklist().unwrap();
        assert_eq!(reloaded.value, state);
        assert!(!reloaded.can_undo);
        assert_eq!(
            ws.load_sync(&request.group).unwrap().packages[0].id,
            request.targets[1]
        );
    }
}
