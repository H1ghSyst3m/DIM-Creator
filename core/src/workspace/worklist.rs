use super::*;
use crate::worklist::{CreatedPackage, Worklist, WorklistAction, WorklistEntry, WorklistState};
use std::sync::MutexGuard;

pub(super) struct WorklistSession {
    pub(super) value: Worklist,
    pub(super) undo: Option<Vec<WorklistEntry>>,
    pub(super) save_error: Option<AppError>,
    pub(super) warnings: Vec<AppError>,
    pub(super) recovery_error: Option<AppError>,
}

impl WorklistSession {
    pub(super) fn state(&self) -> WorklistState {
        WorklistState {
            value: self.value.clone(),
            can_undo: self.undo.is_some(),
            save_error: self.save_error.clone(),
            warnings: self
                .warnings
                .iter()
                .chain(self.recovery_error.iter())
                .cloned()
                .collect(),
        }
    }
}

impl Workspace {
    pub(super) fn worklist_session(
        &self,
    ) -> Result<MutexGuard<'_, Option<WorklistSession>>, AppError> {
        let mut slot = self.worklist.lock().map_err(|_| "workspaceUnavailable")?;
        if slot.is_none() {
            let path = self.root.join("worklist.json");
            let (packages, _) = self.catalog()?;
            let missing = !exists(&path)? && !exists(&backup_path(&path))?;
            let (mut value, recovered) = if missing {
                (Worklist::default(), false)
            } else {
                load_record(&path, Worklist::validate)?
            };
            let previous = value.clone();
            let directory = self.root.join("packages");
            // Directory presence, not successful metadata loading, determines deletion.
            let present = fs::read_dir(directory)
                .map_err(io_error)?
                .map(|entry| entry.map(|entry| entry.file_name().to_string_lossy().into_owned()))
                .collect::<Result<HashSet<_>, _>>()
                .map_err(io_error)?;
            value.entries.retain(
                |entry| !matches!(entry, WorklistEntry::Package { id } if !present.contains(id)),
            );
            for entry in &mut value.entries {
                if let WorklistEntry::Group { members, .. } = entry {
                    members.retain(|id| present.contains(id));
                }
            }
            value.normalize();
            let assigned: HashSet<_> = value.ids().into_iter().collect();
            value.entries.extend(
                packages
                    .into_iter()
                    .filter(|package| !assigned.contains(&package.id))
                    .map(|package| WorklistEntry::Package { id: package.id }),
            );
            let changed = missing || value != previous;
            if value != previous {
                value.revision += 1;
            }
            let save_error = if changed {
                save_record(&path, &value, Worklist::validate).err()
            } else {
                None
            };
            *slot = Some(WorklistSession {
                value,
                undo: None,
                recovery_error: None,
                save_error,
                warnings: if recovered {
                    vec!["worklistBackupLoaded".into()]
                } else {
                    vec![]
                },
            });
        }
        Ok(slot)
    }

    pub fn reload_sync_worklist(&self) -> Result<WorklistState, AppError> {
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        if session.save_error.is_some() {
            return Err("worklistSavePending".into());
        }
        let (actual, _) = load_record(&self.root.join("worklist.json"), Worklist::validate)?;
        if actual != session.value || session.recovery_error.is_some() {
            session.undo = None;
        }
        session.value = actual;
        session.recovery_error = None;
        Ok(session.state())
    }

    pub fn worklist(&self) -> Result<WorklistState, AppError> {
        Ok(self.worklist_session()?.as_ref().unwrap().state())
    }

    pub fn change_worklist(
        &self,
        revision: u32,
        action: &WorklistAction,
    ) -> Result<WorklistState, AppError> {
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        check_revision(session, revision)?;
        let mut next = session.value.apply(action)?;
        if next == session.value {
            return Ok(session.state());
        }
        next.revision += 1;
        save_record(&self.root.join("worklist.json"), &next, Worklist::validate)?;
        if !matches!(action, WorklistAction::Collapse { .. }) {
            session.undo = Some(session.value.entries.clone());
        }
        session.value = next;
        Ok(session.state())
    }

    pub fn undo_worklist(&self, revision: u32) -> Result<WorklistState, AppError> {
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        check_revision(session, revision)?;
        let Some(mut entries) = session.undo.clone() else {
            return Ok(session.state());
        };
        for entry in &mut entries {
            if let WorklistEntry::Group { id, collapsed, .. } = entry
                && let Some(WorklistEntry::Group {
                    collapsed: current, ..
                }) = session.value.entries.iter().find(|entry| entry.id() == id)
            {
                *collapsed = *current;
            }
        }
        let next = Worklist {
            entries,
            revision: revision + 1,
        };
        save_record(&self.root.join("worklist.json"), &next, Worklist::validate)?;
        session.value = next;
        session.undo = None;
        Ok(session.state())
    }

    pub fn retry_worklist(&self) -> Result<WorklistState, AppError> {
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        if let Some(error) = &session.recovery_error {
            return Err(error.clone());
        }
        if session.save_error.is_some() {
            save_record(
                &self.root.join("worklist.json"),
                &session.value,
                Worklist::validate,
            )?;
            session.save_error = None;
        }
        Ok(session.state())
    }

    pub fn worklist_save_pending(&self) -> bool {
        self.worklist.lock().map_or(true, |slot| {
            slot.as_ref()
                .is_some_and(|session| session.save_error.is_some())
        })
    }

    pub fn create_listed(&self, name: &str) -> Result<CreatedPackage, AppError> {
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        if let Some(error) = &session.recovery_error {
            return Err(error.clone());
        }
        if session.save_error.is_some() {
            return Err("worklistSavePending".into());
        }
        let package = self.create(name)?;
        session.value.entries.push(WorklistEntry::Package {
            id: package.id.clone(),
        });
        session.value.revision += 1;
        session.undo = None;
        session.save_error = save_record(
            &self.root.join("worklist.json"),
            &session.value,
            Worklist::validate,
        )
        .err();
        Ok(CreatedPackage {
            package,
            worklist: session.state(),
        })
    }

    pub fn remove_listed(
        &self,
        id: &str,
        trash: impl FnOnce(&Path) -> Result<(), AppError>,
    ) -> Result<WorklistState, AppError> {
        let mut slot = self.worklist_session()?;
        let session = slot.as_mut().unwrap();
        if let Some(error) = &session.recovery_error {
            return Err(error.clone());
        }
        if !session.value.ids().iter().any(|current| current == id) {
            return Err("invalidWorklistAction".into());
        }
        trash(&self.package_dir(id)?)?;
        session.value.entries.retain(
            |entry| !matches!(entry, WorklistEntry::Package { id: current } if current == id),
        );
        for entry in &mut session.value.entries {
            if let WorklistEntry::Group { members, .. } = entry {
                members.retain(|current| current != id);
            }
        }
        session.value.normalize();
        session.value.revision += 1;
        session.undo = None;
        // A successful trash operation cannot be undone by a record-write failure.
        session.save_error = save_record(
            &self.root.join("worklist.json"),
            &session.value,
            Worklist::validate,
        )
        .err();
        Ok(session.state())
    }
}

fn check_revision(session: &WorklistSession, revision: u32) -> Result<(), AppError> {
    if let Some(error) = &session.recovery_error {
        return Err(error.clone());
    }
    if session.save_error.is_some() {
        return Err("worklistSavePending".into());
    }
    if session.value.revision != revision {
        return Err("worklistChanged".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, Workspace, Vec<Package>) {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::new(dir.path().to_path_buf()).unwrap();
        let packages = (1..=4)
            .map(|n| ws.create(&format!("Package {n}")).unwrap())
            .collect();
        (dir, ws, packages)
    }
    #[test]
    fn persisted_groups_restart_undo_and_package_bytes_stay_independent() {
        let (dir, ws, packages) = fixture();
        let before: Vec<_> = packages
            .iter()
            .map(|p| fs::read(ws.package_dir(&p.id).unwrap().join("package.json")).unwrap())
            .collect();
        let initial = ws.worklist().unwrap();
        let ids = packages[..3].iter().map(|p| p.id.clone()).collect();
        let grouped = ws
            .change_worklist(initial.value.revision, &WorklistAction::Group { ids })
            .unwrap();
        let group = grouped.value.entries[0].id().to_owned();
        let folded = ws
            .change_worklist(
                grouped.value.revision,
                &WorklistAction::Collapse {
                    group,
                    collapsed: true,
                },
            )
            .unwrap();
        assert!(folded.can_undo);
        let reopened = Workspace::new(dir.path().to_path_buf())
            .unwrap()
            .worklist()
            .unwrap();
        assert_eq!(reopened.value, folded.value);
        assert!(!reopened.can_undo);
        for (p, bytes) in packages.iter().zip(before) {
            assert_eq!(
                fs::read(ws.package_dir(&p.id).unwrap().join("package.json")).unwrap(),
                bytes
            );
        }
        let mut edited = packages[0].clone();
        edited.name = "Later edit".into();
        ws.save(&edited).unwrap();
        let undone = ws.undo_worklist(folded.value.revision).unwrap();
        assert_eq!(undone.value.entries, initial.value.entries);
        assert_eq!(ws.package(&edited.id).unwrap().name, "Later edit");
        assert!(
            ws.change_worklist(
                initial.value.revision,
                &WorklistAction::Group {
                    ids: packages.iter().map(|p| p.id.clone()).collect()
                }
            )
            .is_err()
        );
    }
    #[test]
    fn unreadable_packages_remain_assigned_and_actual_removals_normalize() {
        let (dir, ws, packages) = fixture();
        let state = ws.worklist().unwrap();
        ws.change_worklist(
            state.value.revision,
            &WorklistAction::Group {
                ids: packages[..2].iter().map(|p| p.id.clone()).collect(),
            },
        )
        .unwrap();
        fs::write(
            ws.package_dir(&packages[0].id)
                .unwrap()
                .join("package.json"),
            b"broken",
        )
        .unwrap();
        let reopened = Workspace::new(dir.path().to_path_buf()).unwrap();
        assert_eq!(reopened.catalog().unwrap().0.len(), 3);
        assert!(matches!(
            reopened.worklist().unwrap().value.entries[0],
            WorklistEntry::Group { .. }
        ));
        fs::remove_dir_all(ws.package_dir(&packages[0].id).unwrap()).unwrap();
        let reopened = Workspace::new(dir.path().to_path_buf()).unwrap();
        let state = reopened.worklist().unwrap();
        assert_eq!(state.value.ids().len(), 3);
        assert!(matches!(
            state.value.entries[0],
            WorklistEntry::Package { .. }
        ));
    }
    #[test]
    fn partial_trash_continues_and_pending_structure_is_retried_separately() {
        let (dir, ws, packages) = fixture();
        ws.worklist().unwrap();
        let primary = dir.path().join("worklist.json");
        fs::remove_file(&primary).unwrap();
        fs::create_dir(&primary).unwrap();
        let first = ws
            .remove_listed(&packages[0].id, |path| {
                fs::remove_dir_all(path).map_err(io_error)
            })
            .unwrap();
        assert!(first.save_error.is_some());
        assert!(!first.value.ids().contains(&packages[0].id));
        assert!(
            ws.change_worklist(
                first.value.revision,
                &WorklistAction::Group {
                    ids: packages[1..].iter().map(|p| p.id.clone()).collect()
                }
            )
            .is_err()
        );
        let second = ws
            .remove_listed(&packages[1].id, |path| {
                fs::remove_dir_all(path).map_err(io_error)
            })
            .unwrap();
        assert_eq!(second.value.ids().len(), 2);
        assert!(second.save_error.is_some());
        assert!(
            ws.remove_listed(&packages[2].id, |_| Err("operationError".into()))
                .is_err()
        );
        fs::remove_dir(&primary).unwrap();
        assert!(ws.retry_worklist().unwrap().save_error.is_none());
        assert_eq!(
            Workspace::new(dir.path().to_path_buf())
                .unwrap()
                .worklist()
                .unwrap()
                .value
                .ids(),
            second.value.ids()
        );
    }
    #[test]
    fn backup_protection_failed_undo_and_creation_follow_existing_records() {
        let (dir, ws, packages) = fixture();
        let initial = ws.worklist().unwrap();
        let grouped = ws
            .change_worklist(
                initial.value.revision,
                &WorklistAction::Group {
                    ids: packages[..2].iter().map(|p| p.id.clone()).collect(),
                },
            )
            .unwrap();
        let path = dir.path().join("worklist.json");
        let saved = fs::read(&path).unwrap();
        fs::write(&path, b"broken").unwrap();
        let recovered = Workspace::new(dir.path().to_path_buf())
            .unwrap()
            .worklist()
            .unwrap();
        assert_eq!(recovered.value.entries, initial.value.entries);
        assert_eq!(recovered.warnings[0].code, "worklistBackupLoaded");
        fs::write(&path, &saved).unwrap();
        let backup = backup_path(&path);
        fs::write(&backup, br#"{"version":999,"data":{}}"#).unwrap();
        assert!(ws.undo_worklist(grouped.value.revision).is_err());
        assert_eq!(ws.worklist().unwrap().value, grouped.value);
        assert!(ws.worklist().unwrap().can_undo);
        assert_eq!(fs::read(&path).unwrap(), saved);
        fs::remove_file(&backup).unwrap();
        let created = ws.create_listed("New independent").unwrap();
        assert!(!created.worklist.can_undo);
        assert_eq!(
            created.worklist.value.ids().last(),
            Some(&created.package.id)
        );
        let protected = br#"{"version":999,"data":{}}"#;
        fs::write(&path, protected).unwrap();
        assert!(
            Workspace::new(dir.path().to_path_buf())
                .unwrap()
                .worklist()
                .is_err()
        );
        assert_eq!(fs::read(&path).unwrap(), protected);
    }
    #[test]
    fn successful_creation_with_failed_structure_write_remains_visible_and_retryable() {
        let (dir, ws, _) = fixture();
        ws.worklist().unwrap();
        let path = dir.path().join("worklist.json");
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        let created = ws.create_listed("Created despite record failure").unwrap();
        assert!(created.worklist.save_error.is_some());
        assert!(created.worklist.value.ids().contains(&created.package.id));
        assert_eq!(ws.catalog().unwrap().0.len(), 5);
        assert!(ws.create_listed("Must wait").is_err());
        fs::remove_dir(&path).unwrap();
        ws.retry_worklist().unwrap();
        assert_eq!(
            Workspace::new(dir.path().to_path_buf())
                .unwrap()
                .worklist()
                .unwrap()
                .value
                .ids()
                .last(),
            Some(&created.package.id)
        );
    }
    #[test]
    fn collapse_and_noops_preserve_undo_and_surviving_group_fold_state() {
        let (_dir, ws, packages) = fixture();
        let a = ws
            .change_worklist(
                ws.worklist().unwrap().value.revision,
                &WorklistAction::Group {
                    ids: packages[..2].iter().map(|p| p.id.clone()).collect(),
                },
            )
            .unwrap();
        let group = a.value.entries[0].id().to_string();
        let b = ws
            .change_worklist(
                a.value.revision,
                &WorklistAction::Group {
                    ids: packages[2..].iter().map(|p| p.id.clone()).collect(),
                },
            )
            .unwrap();
        let folded = ws
            .change_worklist(
                b.value.revision,
                &WorklistAction::Collapse {
                    group: group.clone(),
                    collapsed: true,
                },
            )
            .unwrap();
        let noop = ws
            .change_worklist(
                folded.value.revision,
                &WorklistAction::Collapse {
                    group: group.clone(),
                    collapsed: true,
                },
            )
            .unwrap();
        assert_eq!(folded.value, noop.value);
        assert!(noop.can_undo);
        let undone = ws.undo_worklist(noop.value.revision).unwrap();
        assert!(matches!(
            &undone.value.entries[0],
            WorklistEntry::Group {
                collapsed: true,
                ..
            }
        ));
        assert!(matches!(
            &undone.value.entries[1],
            WorklistEntry::Package { .. }
        ));
        let removed = ws
            .remove_listed(&packages[0].id, |path| {
                fs::remove_dir_all(path).map_err(io_error)
            })
            .unwrap();
        assert!(!removed.can_undo);
        assert_eq!(
            ws.undo_worklist(removed.value.revision).unwrap().value,
            removed.value
        );
    }
}
