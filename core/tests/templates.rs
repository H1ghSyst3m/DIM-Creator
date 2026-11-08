use dim_creator_core::{
    archives::{ArchiveControl, ArchiveSession, ImportAction},
    model::Settings,
    workspace::{CopyPolicy, ImportDraft, Workspace},
};
use std::{fs, io::Write, path::Path};
use zip::{ZipWriter, write::SimpleFileOptions};

fn archive(path: &Path, entries: &[(&str, &[u8])]) {
    let mut zip = ZipWriter::new(fs::File::create(path).unwrap());
    for (name, bytes) in entries {
        zip.start_file(*name, SimpleFileOptions::default()).unwrap();
        zip.write_all(bytes).unwrap();
    }
    zip.finish().unwrap();
}

#[test]
fn rar4_rar5_and_bzip2_templates_keep_exact_bytes_direct_and_nested() {
    use sevenz_rust2::{
        ArchiveEntry, ArchiveWriter, EncoderConfiguration, EncoderMethod, SourceReader,
    };
    let (dir, workspace, destination) = setup();
    let mut paths = Vec::new();
    for name in ["rar4-solid.rar", "rar5-solid.rar"] {
        let path = dir.path().join(name);
        fs::copy(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/archives")
                .join(name),
            &path,
        )
        .unwrap();
        paths.push(path);
    }
    let seven = dir.path().join("UV Templates.7z");
    let mut writer = ArchiveWriter::new(fs::File::create(&seven).unwrap()).unwrap();
    writer.set_content_methods(vec![EncoderConfiguration::new(EncoderMethod::BZIP2)]);
    writer
        .push_archive_entries(
            vec![ArchiveEntry::new_file("uv.txt")],
            vec![SourceReader::new(std::io::Cursor::new(b"UV".to_vec()))],
        )
        .unwrap();
    writer.finish().unwrap();
    paths.push(seven);
    let originals: Vec<_> = paths.iter().map(|path| fs::read(path).unwrap()).collect();
    for nested in [false, true] {
        let inputs = if nested {
            let outer = dir.path().join("Bundle.zip");
            archive(
                &outer,
                &paths
                    .iter()
                    .zip(&originals)
                    .map(|(path, bytes)| {
                        (
                            path.file_name().unwrap().to_str().unwrap(),
                            bytes.as_slice(),
                        )
                    })
                    .collect::<Vec<_>>(),
            );
            vec![outer]
        } else {
            paths.clone()
        };
        let session = analyze(&workspace, &inputs);
        assert!(
            session
                .view()
                .contents
                .iter()
                .all(|item| item.issue.is_none())
        );
        let control = ArchiveControl::default();
        let plan = workspace
            .plan_import(&session, &template_draft(&session))
            .unwrap();
        assert_eq!(plan.view.templates.len(), 3);
        let result = workspace
            .apply_import(&session, plan, CopyPolicy::Replace, &control, |_| {})
            .unwrap();
        assert!(
            result
                .templates
                .iter()
                .all(|row| row.status == dim_creator_core::workspace::FileStatus::Success)
        );
        // The import only copies verified archive bytes; it does not decode them again.
        assert_eq!(
            control.processed_bytes(),
            originals
                .iter()
                .map(|bytes| bytes.len() as u64)
                .sum::<u64>()
        );
        for (path, bytes) in paths.iter().zip(&originals) {
            assert_eq!(
                &fs::read(destination.join(path.file_name().unwrap())).unwrap(),
                bytes
            );
            assert_eq!(&fs::read(path).unwrap(), bytes);
        }
    }
}

#[test]
fn absent_settings_fields_and_backup_preserve_other_preferences() {
    let (dir, workspace, _) = setup();
    let mut json = serde_json::to_value(workspace.settings().unwrap()).unwrap();
    json.as_object_mut().unwrap().remove("templateDir");
    json.as_object_mut().unwrap().remove("autoSaveTemplates");
    json["language"] = "en".into();
    let missing: Settings = serde_json::from_value(json).unwrap();
    assert!(missing.template_dir.is_none());
    assert!(!missing.auto_save_templates);
    assert_eq!(missing.language, "en");
    let mut settings = workspace.settings().unwrap();
    settings.auto_save_templates = true;
    workspace.save_settings(&settings).unwrap();
    workspace
        .save_settings(&Settings {
            language: "en".into(),
            ..settings.clone()
        })
        .unwrap();
    fs::write(workspace.root().join("settings.json"), b"broken").unwrap();
    drop(workspace);
    let reopened = Workspace::new(dir.path().join("workspace")).unwrap();
    assert_eq!(reopened.settings().unwrap(), settings);
}

#[test]
fn missing_directory_and_directory_targets_are_not_replaceable() {
    let (dir, workspace, destination) = setup();
    let path = dir.path().join("Template.zip");
    archive(&path, &[("uv.txt", b"UV")]);
    let session = analyze(&workspace, &[path]);
    fs::create_dir(destination.join("Template.zip")).unwrap();
    assert_eq!(
        workspace
            .plan_import(&session, &template_draft(&session))
            .err()
            .unwrap()
            .code,
        "templateDestinationType"
    );
    let mut settings = workspace.settings().unwrap();
    settings.template_dir = Some(dir.path().join("missing").to_string_lossy().into());
    workspace.save_settings(&settings).unwrap();
    assert!(
        workspace
            .plan_import(&session, &template_draft(&session))
            .is_err()
    );
    assert!(!dir.path().join("missing").exists());
}

#[test]
fn skipping_requires_the_reviewed_destination_to_still_exist() {
    let (dir, workspace, destination) = setup();
    let source = dir.path().join("Template.zip");
    archive(&source, &[("uv.txt", b"UV")]);
    let target = destination.join("Template.zip");
    fs::write(&target, b"existing").unwrap();
    let session = analyze(&workspace, &[source]);
    let plan = workspace
        .plan_import(&session, &template_draft(&session))
        .unwrap();
    fs::remove_file(&target).unwrap();
    let result = workspace
        .apply_import(
            &session,
            plan,
            CopyPolicy::Skip,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert_eq!(
        result.templates[0]
            .error
            .as_ref()
            .map(|error| error.code.as_str()),
        Some("destinationChanged")
    );
    assert!(result.templates[0].reason.is_none());
    assert!(!target.exists());
}

#[cfg(windows)]
#[test]
fn locked_template_failure_preserves_other_successes_and_existing_content() {
    use std::os::windows::fs::OpenOptionsExt;
    let (dir, workspace, destination) = setup();
    let paths: Vec<_> = ["One.zip", "Two.zip"]
        .iter()
        .map(|name| {
            let path = dir.path().join(name);
            archive(&path, &[("uv.txt", b"UV")]);
            path
        })
        .collect();
    fs::write(destination.join("One.zip"), b"old").unwrap();
    let session = analyze(&workspace, &paths);
    let plan = workspace
        .plan_import(&session, &template_draft(&session))
        .unwrap();
    let _lock = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(destination.join("One.zip"))
        .unwrap();
    let result = workspace
        .apply_import(
            &session,
            plan,
            CopyPolicy::Replace,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert_eq!(
        result.templates[0].status,
        dim_creator_core::workspace::FileStatus::Failed
    );
    assert_eq!(
        result.templates[1].status,
        dim_creator_core::workspace::FileStatus::Success
    );
    drop(_lock);
    assert_eq!(fs::read(destination.join("One.zip")).unwrap(), b"old");
    assert_eq!(
        fs::read(destination.join("Two.zip")).unwrap(),
        fs::read(&paths[1]).unwrap()
    );
    assert_eq!(fs::read_dir(destination).unwrap().count(), 2);
}

fn template_draft(session: &ArchiveSession) -> ImportDraft {
    serde_json::from_value(serde_json::json!({
        "assignments": session.view().contents.iter().map(|item| serde_json::json!({
            "contentId": item.id, "rootId": "", "targetId": "",
            "action": "template"
        })).collect::<Vec<_>>(), "targets": [], "groups": []
    }))
    .unwrap()
}

fn setup() -> (tempfile::TempDir, Workspace, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Workspace::new(dir.path().join("workspace")).unwrap();
    let destination = dir.path().join("templates");
    fs::create_dir(&destination).unwrap();
    workspace
        .save_settings(&Settings {
            template_dir: Some(destination.to_string_lossy().into()),
            ..Settings::default()
        })
        .unwrap();
    (dir, workspace, destination)
}

fn analyze(workspace: &Workspace, paths: &[std::path::PathBuf]) -> ArchiveSession {
    ArchiveSession::analyze(
        paths,
        workspace.root(),
        &workspace.settings().unwrap(),
        &ArchiveControl::default(),
        |_| {},
    )
    .unwrap()
}

#[test]
fn nested_template_uses_inner_archive_bytes_and_safety_errors_do_not_hide_valid_content() {
    let (dir, workspace, destination) = setup();
    let inner = dir.path().join("Inner Templates.zip");
    archive(&inner, &[("uv.txt", b"UV")]);
    let bytes = fs::read(&inner).unwrap();
    let outer = dir.path().join("Collection.zip");
    archive(
        &outer,
        &[
            ("People/a.duf", b"content"),
            ("Inner Templates.zip", &bytes),
            ("Broken.zip", b"broken"),
        ],
    );
    let session = analyze(&workspace, &[outer]);
    let view = session.view();
    let content = view
        .contents
        .iter()
        .find(|item| item.archive_name == "Collection.zip")
        .unwrap();
    assert!(content.issue.is_none());
    assert_eq!(
        content.template_issue.as_ref().unwrap().code,
        "templateUnsafeContents"
    );
    assert_eq!(
        session
            .files(&content.id, &content.roots[0].id)
            .unwrap()
            .len(),
        1
    );
    let mut draft = template_draft(&session);
    for (assignment, content) in draft.assignments.iter_mut().zip(&view.contents) {
        if content.archive_name != "Inner Templates.zip" {
            assignment.action = ImportAction::Exclude;
        }
    }
    let plan = workspace.plan_import(&session, &draft).unwrap();
    let result = workspace
        .apply_import(
            &session,
            plan,
            CopyPolicy::Cancel,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert!(result.templates[0].error.is_none());
    assert_eq!(
        fs::read(destination.join("Inner Templates.zip")).unwrap(),
        bytes
    );
    assert_eq!(fs::read_dir(destination).unwrap().count(), 1);
    draft.assignments[0].action = ImportAction::Template;
    assert_eq!(
        workspace.plan_import(&session, &draft).err().unwrap().code,
        "templateUnsafeContents"
    );
}

#[test]
fn template_automation_requires_configuration_and_content_always_wins() {
    let (dir, workspace, _) = setup();
    let plain = dir.path().join("Product Templates.zip");
    let content = dir.path().join("Content Template.zip");
    archive(&plain, &[("uv.txt", b"UV")]);
    archive(&content, &[("People/a.duf", b"content")]);
    for (enabled, configured, expected) in [
        (false, true, ImportAction::Exclude),
        (true, false, ImportAction::Exclude),
        (true, true, ImportAction::Template),
    ] {
        let mut settings = workspace.settings().unwrap();
        settings.template_dir =
            configured.then(|| dir.path().join("templates").to_string_lossy().into());
        settings.auto_save_templates = enabled;
        workspace.save_settings(&settings).unwrap();
        let view = analyze(&workspace, &[plain.clone(), content.clone()]).view();
        assert_eq!(view.contents[0].suggested_action, expected);
        assert_eq!(view.contents[1].suggested_action, ImportAction::Package);
    }
}

#[test]
fn multiple_content_units_share_one_archive_copy_and_cannot_mix_actions() {
    let (dir, workspace, destination) = setup();
    let path = dir.path().join("Collection.zip");
    archive(&path, &[("A/People/a.duf", b"A"), ("B/Props/b.duf", b"B")]);
    let session = analyze(&workspace, std::slice::from_ref(&path));
    let view = session.view();
    assert_eq!(view.contents.len(), 2);
    assert_eq!(view.contents[0].archive_id, view.contents[1].archive_id);
    let mut draft = template_draft(&session);
    let plan = workspace.plan_import(&session, &draft).unwrap();
    assert_eq!(plan.view.templates.len(), 1);
    workspace
        .apply_import(
            &session,
            plan,
            CopyPolicy::Cancel,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert_eq!(
        fs::read(destination.join("Collection.zip")).unwrap(),
        fs::read(path).unwrap()
    );
    draft.assignments[1].action = ImportAction::Exclude;
    assert_eq!(
        workspace.plan_import(&session, &draft).err().unwrap().code,
        "archiveMixedTemplate"
    );
}

#[test]
fn collisions_require_consent_and_report_only_the_selected_winner() {
    for policy in [CopyPolicy::Replace, CopyPolicy::Skip, CopyPolicy::Cancel] {
        for preexisting in [false, true] {
            let (dir, workspace, destination) = setup();
            fs::create_dir(dir.path().join("second")).unwrap();
            let first = dir.path().join("Shared.zip");
            let last = dir.path().join("second/shared.zip");
            archive(&first, &[("first.txt", b"first")]);
            archive(&last, &[("last.txt", b"last")]);
            if preexisting {
                fs::write(destination.join("SHARED.ZIP"), b"original").unwrap();
            }
            let session = analyze(&workspace, &[first.clone(), last.clone()]);
            let plan = workspace
                .plan_import(&session, &template_draft(&session))
                .unwrap();
            assert!(
                plan.view
                    .templates
                    .iter()
                    .all(|item| !item.conflicts.is_empty())
            );
            let preview = serde_json::to_value(&plan.view).unwrap();
            assert_eq!(
                preview["templates"][0]["sourceLocation"],
                first.to_string_lossy().as_ref()
            );
            assert_eq!(
                preview["templates"][1]["sourceLocation"],
                last.to_string_lossy().as_ref()
            );
            for (index, item) in preview["templates"].as_array().unwrap().iter().enumerate() {
                assert_eq!(item["existingPath"].is_string(), preexisting);
                assert_eq!(
                    item["conflictingArchives"],
                    serde_json::json!([plan.view.templates[1 - index].archive_id])
                );
            }
            let result = workspace
                .apply_import(&session, plan, policy, &ArchiveControl::default(), |_| {})
                .unwrap();
            let saved = result
                .templates
                .iter()
                .filter(|item| item.status == dim_creator_core::workspace::FileStatus::Success)
                .count();
            match policy {
                CopyPolicy::Replace => {
                    assert_eq!(saved, 1);
                    assert_eq!(
                        fs::read(destination.join("shared.zip")).unwrap(),
                        fs::read(last).unwrap()
                    );
                }
                CopyPolicy::Skip if !preexisting => {
                    assert_eq!(saved, 1);
                    assert_eq!(
                        fs::read(destination.join("Shared.zip")).unwrap(),
                        fs::read(first).unwrap()
                    );
                }
                _ => {
                    assert_eq!(saved, 0);
                    if preexisting {
                        assert_eq!(
                            fs::read(destination.join("SHARED.ZIP")).unwrap(),
                            b"original"
                        );
                    }
                }
            }
            let names: Vec<_> = fs::read_dir(&destination)
                .unwrap()
                .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
                .collect();
            assert_eq!(names.len(), usize::from(saved > 0 || preexisting));
            if policy == CopyPolicy::Replace {
                assert_eq!(names, ["shared.zip"]);
            }
        }
    }
}

#[test]
fn destination_changes_and_forbidden_destinations_are_not_overwritten() {
    let (dir, workspace, destination) = setup();
    let source = dir.path().join("Template.zip");
    archive(&source, &[("uv.txt", b"UV")]);
    let session = analyze(&workspace, std::slice::from_ref(&source));
    let draft = template_draft(&session);
    let plan = workspace.plan_import(&session, &draft).unwrap();
    fs::write(destination.join("Template.zip"), b"appeared after review").unwrap();
    let result = workspace
        .apply_import(
            &session,
            plan,
            CopyPolicy::Replace,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert_eq!(
        result.templates[0].error.as_ref().unwrap().code,
        "destinationChanged"
    );
    assert_eq!(
        fs::read(destination.join("Template.zip")).unwrap(),
        b"appeared after review"
    );
    for (path, code) in [
        (workspace.root().to_path_buf(), "templateInsideWorkspace"),
        (dir.path().to_path_buf(), "templateReplacesSource"),
    ] {
        let mut settings = workspace.settings().unwrap();
        settings.template_dir = Some(path.to_string_lossy().into());
        workspace.save_settings(&settings).unwrap();
        assert_eq!(
            workspace.plan_import(&session, &draft).err().unwrap().code,
            code
        );
    }
}

#[test]
fn cancellation_preserves_completed_templates_and_leaves_no_staging() {
    let (dir, workspace, destination) = setup();
    let paths: Vec<_> = ["One.zip", "Two.zip"]
        .iter()
        .map(|name| {
            let path = dir.path().join(name);
            archive(&path, &[("uv.txt", b"UV")]);
            path
        })
        .collect();
    let session = analyze(&workspace, &paths);
    let plan = workspace
        .plan_import(&session, &template_draft(&session))
        .unwrap();
    let control = ArchiveControl::default();
    let result = workspace
        .apply_import(&session, plan, CopyPolicy::Cancel, &control, |name| {
            if name == "Two.zip" {
                control.cancel();
            }
        })
        .unwrap();
    assert_eq!(
        result.templates[0].status,
        dim_creator_core::workspace::FileStatus::Success
    );
    assert_eq!(
        result.templates[1].error.as_ref().unwrap().code,
        "archiveCancelled"
    );
    assert_eq!(fs::read_dir(&destination).unwrap().count(), 1);
    assert!(destination.join("One.zip").is_file());
}

#[test]
fn template_only_import_preserves_worklist_bytes_revision_and_undo() {
    use dim_creator_core::worklist::WorklistAction;
    let (dir, workspace, _) = setup();
    let a = workspace.create_listed("A").unwrap().package;
    let b = workspace.create_listed("B").unwrap().package;
    let state = workspace.worklist().unwrap();
    let grouped = workspace
        .change_worklist(
            state.value.revision,
            &WorklistAction::Group {
                ids: vec![a.id, b.id],
            },
        )
        .unwrap();
    assert!(grouped.can_undo);
    let before = ["worklist.json", "worklist.backup.json"]
        .map(|name| fs::read(workspace.root().join(name)).unwrap());
    let path = dir.path().join("Template.zip");
    archive(&path, &[("uv.txt", b"UV")]);
    let session = analyze(&workspace, &[path]);
    let plan = workspace
        .plan_import(&session, &template_draft(&session))
        .unwrap();
    let result = workspace
        .apply_import(
            &session,
            plan,
            CopyPolicy::Cancel,
            &ArchiveControl::default(),
            |_| {},
        )
        .unwrap();
    assert!(result.worklist.is_none());
    assert_eq!(
        before,
        ["worklist.json", "worklist.backup.json"]
            .map(|name| fs::read(workspace.root().join(name)).unwrap())
    );
    assert_eq!(workspace.worklist().unwrap().value, grouped.value);
    assert!(workspace.worklist().unwrap().can_undo);
}

#[test]
fn template_only_copy_keeps_archive_bytes_and_does_not_initialize_worklist() {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Workspace::new(dir.path().join("workspace")).unwrap();
    let destination = dir.path().join("templates");
    fs::create_dir(&destination).unwrap();
    let mut settings = serde_json::to_value(Settings::default()).unwrap();
    settings["templateDir"] = destination.to_string_lossy().as_ref().into();
    workspace
        .save_settings(&serde_json::from_value(settings).unwrap())
        .unwrap();
    let path = dir.path().join("Product Templates.zip");
    archive(&path, &[("textures.txt", b"template")]);
    let original = fs::read(&path).unwrap();
    let control = ArchiveControl::default();
    let session = ArchiveSession::analyze(
        std::slice::from_ref(&path),
        workspace.root(),
        &workspace.settings().unwrap(),
        &control,
        |_| {},
    )
    .unwrap();
    let plan = workspace
        .plan_import(&session, &template_draft(&session))
        .unwrap();
    assert_eq!(fs::read_dir(&destination).unwrap().count(), 0);
    let result = workspace
        .apply_import(&session, plan, CopyPolicy::Cancel, &control, |_| {})
        .unwrap();
    let result = serde_json::to_value(result).unwrap();
    assert_eq!(result["templates"][0]["status"], "success");
    assert!(result["worklist"].is_null());
    assert!(!workspace.root().join("worklist.json").exists());
    assert_eq!(
        fs::read_dir(workspace.root().join("packages"))
            .unwrap()
            .count(),
        0
    );
    assert_eq!(
        fs::read(destination.join("Product Templates.zip")).unwrap(),
        original
    );
    assert_eq!(fs::read(path).unwrap(), original);
}

#[test]
fn template_settings_preserve_defaults_and_offline_directory() {
    let dir = tempfile::tempdir().unwrap();
    let workspace = Workspace::new(dir.path().join("workspace")).unwrap();
    let mut json = serde_json::to_value(Settings::default()).unwrap();
    assert_eq!(json["autoSaveTemplates"], false);
    assert!(json["templateDir"].is_null());
    json["templateDir"] = dir.path().join("offline").to_string_lossy().as_ref().into();
    json["autoSaveTemplates"] = true.into();
    let settings = serde_json::from_value(json.clone()).unwrap();
    workspace.save_settings(&settings).unwrap();
    assert_eq!(
        serde_json::to_value(workspace.settings().unwrap()).unwrap(),
        json
    );
}
