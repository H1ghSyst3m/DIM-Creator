use dim_creator_core::{model::Package, package, synchronization::*};

#[test]
fn metadata_preview_accepts_drafts_without_opening_content() {
    let mut item = item("Draft");
    item.name.clear();
    let preview = package::metadata_preview(&item);
    assert!(preview.filename.is_none());
    assert!(
        preview
            .issues
            .iter()
            .any(|issue| issue.error.code == "required")
    );
    assert!(!preview.issues.iter().any(|issue| issue.field == "content"));
}

#[test]
fn transfer_copies_exact_values_and_numbers_the_entire_group() {
    let mut source = item("Source");
    source.prefix = "CUSTOM".into();
    source.auto_prefix = false;
    source.clean_support = false;
    source.tags.clear();
    let target = item("Target");
    let packages = vec![source.clone(), target.clone()];
    let order = vec![target.id.clone(), source.id.clone()];
    let changes = plan_changes(
        &packages,
        &source.id,
        std::slice::from_ref(&target.id),
        &[
            SyncField::Store,
            SyncField::Sku,
            SyncField::Tags,
            SyncField::Name,
            SyncField::Support,
        ],
        Some(&order),
    )
    .unwrap();
    let copied = &changes
        .iter()
        .find(|change| change.after.id == target.id)
        .unwrap()
        .after;
    assert_eq!(copied.prefix, "CUSTOM");
    assert!(!copied.auto_prefix);
    assert!(!copied.clean_support);
    assert!(copied.tags.is_empty());
    assert!(copied.sku.is_empty());
    assert_eq!(copied.guid, target.guid);
    assert_eq!(copied.part, "01");
    assert_eq!(
        changes
            .iter()
            .find(|change| change.after.id == source.id)
            .unwrap()
            .after
            .part,
        "02"
    );
    assert!(
        plan_changes(
            &packages,
            &source.id,
            &[],
            &[],
            Some(&[source.id.clone(), source.id.clone()])
        )
        .is_err()
    );
    assert!(
        plan_changes(&packages, &source.id, &[], &[], None)
            .unwrap()
            .is_empty()
    );
}

fn item(name: &str) -> Package {
    Package {
        id: uuid::Uuid::new_v4().to_string(),
        name: name.into(),
        store: "DAZ 3D".into(),
        prefix: "IM".into(),
        auto_prefix: true,
        sku: String::new(),
        part: "01".into(),
        guid: uuid::Uuid::new_v4().to_string(),
        tags: vec!["DAZStudio4_5".into()],
        cover: None,
        clean_support: true,
        excluded: vec![],
    }
}

#[test]
fn synchronization_preserves_worklist_bytes_and_does_not_scan_content() {
    use dim_creator_core::{worklist::WorklistAction, workspace::Workspace};
    let dir = tempfile::tempdir().unwrap();
    let ws = Workspace::new(dir.path().to_path_buf()).unwrap();
    let a = ws.create_listed("Source").unwrap().package;
    let b = ws.create_listed("Target").unwrap().package;
    let state = ws
        .change_worklist(
            ws.worklist().unwrap().value.revision,
            &WorklistAction::Group {
                ids: vec![a.id.clone(), b.id.clone()],
            },
        )
        .unwrap();
    let group = state.value.entries[0].id().to_string();
    let before: Vec<_> = ["worklist.json", "worklist.backup.json"]
        .map(|name| std::fs::read(dir.path().join(name)).unwrap())
        .into();
    // An absent Content directory makes any accidental inventory scan observable.
    std::fs::remove_dir(ws.package_dir(&b.id).unwrap().join("Content")).unwrap();
    let loaded = ws.load_sync(&group).unwrap();
    let request = SyncRequest {
        group,
        source: a.id.clone(),
        targets: vec![b.id.clone()],
        fields: vec![SyncField::Name],
        order: None,
        expected: loaded.expected,
    };
    let plan = ws.plan_sync(&request).unwrap();
    assert_eq!(plan.changes.len(), 1);
    let result = ws.apply_sync_plan(plan).unwrap();
    assert!(result.error.is_none());
    assert!(!result.worklist.can_undo);
    assert_eq!(result.worklist.value.revision, state.value.revision);
    for (name, bytes) in ["worklist.json", "worklist.backup.json"]
        .into_iter()
        .zip(before)
    {
        assert_eq!(std::fs::read(dir.path().join(name)).unwrap(), bytes);
    }
    let saved = ws.package(&b.id).unwrap();
    assert_eq!(saved.name, "Source");
    assert_eq!(saved.guid, b.guid);
    assert!(ws.plan_sync(&request).is_err());
    let restarted = Workspace::new(dir.path().to_path_buf()).unwrap();
    assert_eq!(restarted.package(&b.id).unwrap(), saved);
}

fn grouped() -> (
    tempfile::TempDir,
    dim_creator_core::workspace::Workspace,
    SyncRequest,
) {
    use dim_creator_core::{worklist::WorklistAction, workspace::Workspace};
    let dir = tempfile::tempdir().unwrap();
    let ws = Workspace::new(dir.path().to_path_buf()).unwrap();
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
    let group = state.value.entries[0].id().to_string();
    let expected = ws.load_sync(&group).unwrap().expected;
    (
        dir,
        ws,
        SyncRequest {
            group,
            source: ids[0].clone(),
            targets: ids[1..].to_vec(),
            fields: vec![],
            order: None,
            expected,
        },
    )
}

#[test]
fn part_values_do_not_write_structure_but_changed_order_writes_once() {
    let (dir, ws, mut request) = grouped();
    let ids = ws
        .load_sync(&request.group)
        .unwrap()
        .packages
        .iter()
        .map(|p| p.id.clone())
        .collect::<Vec<_>>();
    let files = || {
        ["worklist.json", "worklist.backup.json"]
            .map(|name| std::fs::read(dir.path().join(name)).unwrap())
    };
    let original = files();
    let revision = request.expected.revision;
    request.order = Some(ids.clone());
    let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
    assert_eq!(files(), original);
    assert_eq!(result.worklist.value.revision, revision);
    assert_eq!(
        result
            .packages
            .iter()
            .map(|p| p.part.as_str())
            .collect::<Vec<_>>(),
        vec!["01", "02", "03"]
    );
    request.expected = ws.load_sync(&request.group).unwrap().expected;
    request.order.as_mut().unwrap().reverse();
    let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
    assert_eq!(result.worklist.value.revision, revision + 1);
    assert_eq!(
        result.worklist.value.entries[0].ids(),
        request.order.as_ref().unwrap()
    );
    assert_eq!(files()[1], original[0]);
    assert!(!result.worklist.can_undo);
    request.expected = ws.load_sync(&request.group).unwrap().expected;
    let before = files();
    assert!(ws.plan_sync(&request).unwrap().changes.is_empty());
    ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
    assert_eq!(files(), before);
}

#[test]
fn cover_transfer_is_byte_identical_independent_and_validated_without_sku() {
    let (dir, ws, mut request) = grouped();
    let image = dir.path().join("source.png");
    image::RgbImage::from_pixel(3, 2, image::Rgb([20, 100, 160]))
        .save(&image)
        .unwrap();
    ws.set_cover(&request.source, Some(&image)).unwrap();
    request.expected = ws.load_sync(&request.group).unwrap().expected;
    request.fields = vec![SyncField::Cover];
    let before = ws.package(&request.source).unwrap();
    let source = std::fs::read(
        ws.package_dir(&before.id)
            .unwrap()
            .join(before.cover.as_ref().unwrap()),
    )
    .unwrap();
    let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
    let names = result
        .packages
        .iter()
        .map(|p| p.cover.as_ref().unwrap())
        .collect::<std::collections::HashSet<_>>();
    assert_eq!(names.len(), 3);
    for p in &result.packages {
        assert!(p.sku.is_empty());
        assert_eq!(
            std::fs::read(
                ws.package_dir(&p.id)
                    .unwrap()
                    .join(p.cover.as_ref().unwrap())
            )
            .unwrap(),
            source
        );
    }
    request.expected = ws.load_sync(&request.group).unwrap().expected;
    assert!(ws.plan_sync(&request).unwrap().changes.is_empty());
    // No cover reads when only metadata is requested.
    std::fs::write(
        ws.package_dir(&before.id)
            .unwrap()
            .join(before.cover.as_ref().unwrap()),
        b"broken",
    )
    .unwrap();
    request.fields = vec![SyncField::Name];
    assert!(ws.plan_sync(&request).is_ok());
    request.fields = vec![SyncField::Cover];
    assert!(ws.plan_sync(&request).is_err());
    ws.set_cover(&request.source, None).unwrap();
    let target = ws.package(&request.targets[0]).unwrap();
    std::fs::remove_file(
        ws.package_dir(&target.id)
            .unwrap()
            .join(target.cover.unwrap()),
    )
    .unwrap();
    request.expected = ws.load_sync(&request.group).unwrap().expected;
    let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).unwrap();
    assert!(result.packages.iter().all(|p| p.cover.is_none()));
    // The backup still references the former target cover.
    let backup: serde_json::Value = serde_json::from_slice(
        &std::fs::read(
            ws.package_dir(&request.targets[1])
                .unwrap()
                .join("package.backup.json"),
        )
        .unwrap(),
    )
    .unwrap();
    assert!(
        ws.package_dir(&request.targets[1])
            .unwrap()
            .join(backup["data"]["cover"].as_str().unwrap())
            .exists()
    );
}

#[test]
fn protected_records_and_stale_disk_worklist_are_never_overwritten() {
    let (dir, ws, mut request) = grouped();
    request.fields = vec![SyncField::Name];
    let protected = ws
        .package_dir(&request.targets[1])
        .unwrap()
        .join("package.backup.json");
    std::fs::write(&protected, br#"{"version":999,"data":{}}"#).unwrap();
    request.expected = ws.load_sync(&request.group).unwrap().expected;
    let before = ws.package(&request.targets[0]).unwrap();
    assert!(ws.apply_sync_plan(ws.plan_sync(&request).unwrap()).is_err());
    assert_eq!(ws.package(&before.id).unwrap(), before);
    assert!(ws.worklist().unwrap().can_undo);
    let path = dir.path().join("worklist.json");
    let mut document: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    document["data"]["entries"][0]["members"]
        .as_array_mut()
        .unwrap()
        .reverse();
    std::fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
    assert_eq!(ws.plan_sync(&request).err().unwrap().code, "syncStale");
}

#[test]
fn numbering_limit_is_independent_of_metadata_targets() {
    let packages: Vec<_> = (0..100).map(|n| item(&format!("P{n}"))).collect();
    let order = packages.iter().map(|p| p.id.clone()).collect::<Vec<_>>();
    assert_eq!(
        plan_changes(&packages, &packages[0].id, &[], &[], Some(&order))
            .unwrap_err()
            .code,
        "syncPartLimit"
    );
    assert!(
        plan_changes(
            &packages,
            &packages[0].id,
            &[packages[1].id.clone()],
            &[SyncField::Name],
            None
        )
        .is_ok()
    );
    assert!(
        plan_changes(
            &packages[..99],
            &packages[0].id,
            &[],
            &[],
            Some(&order[..99])
        )
        .is_ok()
    );
}

#[cfg(windows)]
#[test]
fn failed_later_write_rolls_back_primary_backup_and_preserves_undo() {
    let (_dir, ws, mut request) = grouped();
    request.fields = vec![SyncField::Name];
    let snapshots = request
        .targets
        .iter()
        .map(|id| {
            let dir = ws.package_dir(id).unwrap();
            (
                dir.clone(),
                std::fs::read(dir.join("package.json")).unwrap(),
            )
        })
        .collect::<Vec<_>>();
    let locked =
        dim_creator_core::filesystem::open_regular_file(&snapshots[1].0.join("package.json"))
            .unwrap();
    let result = ws.apply_sync_plan(ws.plan_sync(&request).unwrap());
    assert!(result.is_err());
    drop(locked);
    for (dir, bytes) in snapshots {
        assert_eq!(std::fs::read(dir.join("package.json")).unwrap(), bytes);
        assert!(!dir.join("package.backup.json").exists());
    }
    assert!(ws.worklist().unwrap().can_undo);
    assert!(
        ws.apply_sync_plan(ws.plan_sync(&request).unwrap())
            .unwrap()
            .error
            .is_none()
    );
}
