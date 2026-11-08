use dim_creator_core::{
    archives::{ArchiveControl, ArchiveSession, ImportAction},
    workspace::{CopyPolicy, ImportAssignment, ImportDraft, ImportGroup, ImportTarget, Workspace},
};
use std::{fs, io::Write};
use uuid::Uuid;
use zip::{ZipWriter, write::SimpleFileOptions};

#[test]
fn multiple_archives_share_one_target_part_and_import_keeps_existing_metadata() {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Workspace::new(dir.path().join("workspace")).unwrap();
    let existing = workspace.create_listed("Existing").unwrap().package;
    let original = fs::read(
        workspace
            .package_dir(&existing.id)
            .unwrap()
            .join("package.json"),
    )
    .unwrap();
    let mut paths = Vec::new();
    for (name, content) in [("One", "one"), ("Two", "two"), ("Three", "three")] {
        let path = dir.path().join(format!("{name}.zip"));
        let mut zip = ZipWriter::new(fs::File::create(&path).unwrap());
        zip.start_file(
            format!("Library/People/{content}.duf"),
            SimpleFileOptions::default(),
        )
        .unwrap();
        zip.write_all(content.as_bytes()).unwrap();
        zip.finish().unwrap();
        paths.push(path);
    }
    let control = ArchiveControl::default();
    let session = ArchiveSession::analyze(
        &paths,
        dir.path(),
        &workspace.settings().unwrap(),
        &control,
        |_| {},
    )
    .unwrap();
    let view = session.view();
    let existing_target = Uuid::new_v4().to_string();
    let new_target = Uuid::new_v4().to_string();
    let draft = ImportDraft {
        assignments: view
            .contents
            .iter()
            .enumerate()
            .map(|(index, content)| ImportAssignment {
                content_id: content.id.clone(),
                root_id: content.roots[0].id.clone(),
                target_id: if index == 0 {
                    existing_target.clone()
                } else {
                    new_target.clone()
                },
                action: ImportAction::Package,
            })
            .collect(),
        targets: vec![
            ImportTarget {
                id: existing_target.clone(),
                package_id: Some(existing.id.clone()),
                name: "ignored".into(),
            },
            ImportTarget {
                id: new_target.clone(),
                package_id: None,
                name: "New product".into(),
            },
        ],
        groups: vec![ImportGroup {
            id: Uuid::new_v4().to_string(),
            targets: vec![existing_target, new_target],
        }],
    };
    let plan = workspace.plan_import(&session, &draft).unwrap();
    assert_eq!(plan.view.targets.len(), 2);
    assert_eq!(plan.view.targets[1].package.part, "02");
    assert_eq!(plan.view.targets[1].sources.len(), 2);
    let result = workspace
        .apply_import(&session, plan, CopyPolicy::Cancel, &control, |_| {})
        .unwrap();
    assert!(result.results.iter().all(|entry| entry.error.is_none()));
    assert_eq!(result.packages.len(), 2);
    let new = &result.packages[1];
    let content = workspace.folder(&new.id, "People").unwrap();
    assert_eq!(fs::read(content.join("two.duf")).unwrap(), b"two");
    assert_eq!(fs::read(content.join("three.duf")).unwrap(), b"three");
    assert_eq!(
        fs::read(
            workspace
                .package_dir(&existing.id)
                .unwrap()
                .join("package.json")
        )
        .unwrap(),
        original
    );
    assert_eq!(result.worklist.as_ref().unwrap().value.entries.len(), 1);
    let reopened = Workspace::new(workspace.root().into()).unwrap();
    assert_eq!(
        reopened.worklist().unwrap().value,
        result.worklist.as_ref().unwrap().value
    );
}

fn fixture() -> (tempfile::TempDir, Workspace, ArchiveSession, ImportDraft) {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Workspace::new(dir.path().join("workspace")).unwrap();
    let mut paths = Vec::new();
    for (name, bytes) in [("One", b"one"), ("Two", b"two")] {
        let path = dir.path().join(format!("{name}.zip"));
        let mut zip = ZipWriter::new(fs::File::create(&path).unwrap());
        zip.start_file("People/item.duf", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(bytes).unwrap();
        zip.finish().unwrap();
        paths.push(path);
    }
    let session = ArchiveSession::analyze(
        &paths,
        dir.path(),
        &workspace.settings().unwrap(),
        &ArchiveControl::default(),
        |_| {},
    )
    .unwrap();
    let targets: Vec<_> = ["One", "Two"]
        .iter()
        .map(|name| ImportTarget {
            id: Uuid::new_v4().to_string(),
            package_id: None,
            name: (*name).into(),
        })
        .collect();
    let assignments = session
        .view()
        .contents
        .iter()
        .zip(&targets)
        .map(|(content, target)| ImportAssignment {
            content_id: content.id.clone(),
            root_id: content.roots[0].id.clone(),
            target_id: target.id.clone(),
            action: ImportAction::Package,
        })
        .collect();
    (
        dir,
        workspace,
        session,
        ImportDraft {
            assignments,
            targets,
            groups: vec![],
        },
    )
}

#[test]
fn source_conflicts_follow_confirmed_order_and_policy() {
    for (policy, expected) in [(CopyPolicy::Replace, b"two"), (CopyPolicy::Skip, b"one")] {
        let (_dir, ws, session, mut draft) = fixture();
        draft.assignments[1].target_id = draft.targets[0].id.clone();
        let plan = ws.plan_import(&session, &draft).unwrap();
        assert_eq!(plan.view.targets[0].conflicts, ["People/item.duf"]);
        let result = ws
            .apply_import(&session, plan, policy, &ArchiveControl::default(), |_| {})
            .unwrap();
        assert_eq!(result.results[0].imported_files, 1);
        assert_eq!(result.results[0].skipped_files, 1);
        assert_eq!(
            fs::read(
                ws.folder(&result.packages[0].id, "People")
                    .unwrap()
                    .join("item.duf")
            )
            .unwrap(),
            expected
        );
    }
}

#[test]
fn existing_conflicts_require_fresh_approval_and_skip_keeps_original_bytes() {
    let (_dir, ws, session, mut draft) = fixture();
    let existing = ws.create_listed("Existing").unwrap().package;
    ws.create_folder(&existing.id, "", "People").unwrap();
    let file = ws.folder(&existing.id, "People").unwrap().join("item.duf");
    fs::write(&file, b"original").unwrap();
    draft.targets[0].package_id = Some(existing.id.clone());
    draft.assignments[1].action = ImportAction::Exclude;
    let plan = ws.plan_import(&session, &draft).unwrap();
    let result = ws
        .apply_import(
            &session,
            plan,
            CopyPolicy::Skip,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert_eq!(result.results[0].imported_files, 0);
    assert_eq!(result.results[0].skipped_files, 1);
    assert_eq!(fs::read(&file).unwrap(), b"original");
    let plan = ws.plan_import(&session, &draft).unwrap();
    fs::write(&file, b"changed after review").unwrap();
    let result = ws
        .apply_import(
            &session,
            plan,
            CopyPolicy::Replace,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert_eq!(
        result.results[0].error.as_ref().unwrap().code,
        "destinationChanged"
    );
    assert_eq!(fs::read(&file).unwrap(), b"changed after review");
}

#[test]
fn ordered_targets_keep_completed_packages_on_cancel_and_leave_no_staging() {
    let (_dir, ws, session, mut draft) = fixture();
    draft.assignments.reverse();
    let plan = ws.plan_import(&session, &draft).unwrap();
    assert_eq!(plan.view.targets[0].package.name, "Two");
    let control = ArchiveControl::default();
    let mut calls = 0;
    let result = ws
        .apply_import(&session, plan, CopyPolicy::Cancel, &control, |_| {
            calls += 1;
            if calls == 2 {
                control.cancel();
            }
        })
        .unwrap();
    assert_eq!(result.packages.len(), 1);
    assert_eq!(result.packages[0].name, "Two");
    assert_eq!(ws.catalog().unwrap().0.len(), 1);
    assert_eq!(fs::read_dir(ws.root().join("packages")).unwrap().count(), 1);
    assert!(result.results[1].error.is_some());
}

#[test]
fn worklist_save_failure_preserves_published_target_and_stops_later_targets() {
    let (_dir, ws, session, draft) = fixture();
    let plan = ws.plan_import(&session, &draft).unwrap();
    let primary = ws.root().join("worklist.json");
    let result = ws
        .apply_import(
            &session,
            plan,
            CopyPolicy::Cancel,
            &ArchiveControl::default(),
            |_| {
                // Fail the structure write after the starting snapshot has been checked.
                fs::remove_file(&primary).unwrap();
                fs::create_dir(&primary).unwrap();
            },
        )
        .unwrap();
    assert_eq!(result.packages.len(), 1);
    assert!(result.results[0].error.is_none());
    assert_eq!(
        result.results[1].error.as_ref().unwrap().code,
        "worklistSavePending"
    );
    assert!(result.worklist.as_ref().unwrap().save_error.is_some());
    assert_eq!(ws.catalog().unwrap().0.len(), 1);
    assert_eq!(fs::read_dir(ws.root().join("packages")).unwrap().count(), 1);
    fs::remove_dir(&primary).unwrap();
    let restored = ws.retry_worklist().unwrap();
    assert!(restored.save_error.is_none());
    assert_eq!(restored.value.ids(), vec![result.packages[0].id.clone()]);
    let reopened = Workspace::new(ws.root().into()).unwrap();
    assert_eq!(reopened.worklist().unwrap().value, restored.value);
}

#[test]
fn group_extension_checks_all_member_parts_and_rejects_changed_metadata() {
    use dim_creator_core::worklist::WorklistAction;
    let (_dir, ws, session, mut draft) = fixture();
    let first = ws.create_listed("First").unwrap().package;
    let mut second = ws.create_listed("Second").unwrap().package;
    second.part = "07".into();
    ws.save(&second).unwrap();
    ws.change_worklist(
        ws.worklist().unwrap().value.revision,
        &WorklistAction::Group {
            ids: vec![first.id.clone(), second.id.clone()],
        },
    )
    .unwrap();
    draft.targets[0].package_id = Some(first.id.clone());
    draft.groups.push(ImportGroup {
        id: Uuid::new_v4().to_string(),
        targets: draft.targets.iter().map(|t| t.id.clone()).collect(),
    });
    let plan = ws.plan_import(&session, &draft).unwrap();
    assert_eq!(plan.view.targets[1].package.part, "08");
    second.part = "08".into();
    ws.save(&second).unwrap();
    let error = ws
        .apply_import(
            &session,
            plan,
            CopyPolicy::Cancel,
            &ArchiveControl::default(),
            |_| {},
        )
        .err()
        .unwrap();
    assert_eq!(error.code, "archiveSourceChanged");
    second.part = "99".into();
    ws.save(&second).unwrap();
    assert_eq!(
        ws.plan_import(&session, &draft).err().unwrap().code,
        "archivePartLimit"
    );
    assert_eq!(ws.catalog().unwrap().0.len(), 2);
}
