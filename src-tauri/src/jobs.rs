use crate::output::{self, OutputContext};
use dim_creator_core::error::{AppError, Severity};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::atomic::{AtomicBool, Ordering},
};

use dim_creator_core::{
    filesystem as paths,
    model::Package,
    package::{self, Issue, Phase},
    workspace::Workspace,
};
use serde::{Deserialize, Serialize};

#[derive(Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ExportRow {
    pub id: String,
    pub name: String,
    pub filename: Option<String>,
    pub output_dir: Option<PathBuf>,
    pub output_path: Option<PathBuf>,
    pub issues: Vec<Issue>,
    pub existing: bool,
    pub stamp: Option<String>,
}

#[derive(Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "ExportApproval")]
pub struct Replacement {
    id: String,
    output_path: PathBuf,
    stamp: String,
}

impl ExportRow {
    fn error(&mut self, code: &str, error: AppError) {
        self.issues.push(Issue::cause(code, "output", error));
    }

    pub fn ready(&self) -> bool {
        self.issues
            .iter()
            .all(|issue| issue.severity != Severity::Error)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ts_rs::TS)]
#[serde(rename_all = "lowercase")]
pub enum ExportStatus {
    Success,
    Failed,
    Cancelled,
    Skipped,
}

#[derive(Clone, Serialize, ts_rs::TS)]
pub struct ExportResult {
    pub id: String,
    pub status: ExportStatus,
    pub path: Option<PathBuf>,
    pub message: Option<AppError>,
}

#[derive(Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct JobEvent {
    pub job_id: String,
    pub package_id: Option<String>,
    pub phase: Phase,
    pub done: u64,
    pub total: u64,
    pub results: Option<Vec<ExportResult>>,
}

#[derive(Serialize, ts_rs::TS)]
pub struct ExportPreflight {
    pub context: OutputContext,
    pub rows: Vec<ExportRow>,
}

#[derive(Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ExportTarget {
    pub id: String,
    pub output_path: PathBuf,
}

pub fn verify_targets(plans: &mut [(Option<Package>, ExportRow)], targets: &[ExportTarget]) {
    for (_, row) in plans {
        let expected = targets.iter().find(|target| target.id == row.id);
        if !expected.is_some_and(|target| {
            row.output_path
                .as_ref()
                .is_some_and(|path| paths::path_key(path) == paths::path_key(&target.output_path))
        }) {
            row.issues
                .push(Issue::error("outputTargetChanged", "output"));
        }
    }
}

fn mark_collisions(plans: &mut [(Option<Package>, ExportRow)]) {
    let mut counts = HashMap::new();
    for (_, row) in plans.iter() {
        if let Some(path) = &row.output_path {
            *counts.entry(paths::path_key(path)).or_insert(0) += 1;
        }
    }
    for (_, row) in plans {
        if let Some(path) = &row.output_path
            && counts[&paths::path_key(path)] > 1
        {
            row.issues
                .push(Issue::value("duplicateOutput", "output", path.display()));
        }
    }
}

pub fn plan(
    workspace: &Workspace,
    daz_folders: &[String],
    ids: Vec<String>,
    context: &OutputContext,
    create_directories: bool,
    cancelled: &AtomicBool,
) -> Result<Vec<(Option<Package>, ExportRow)>, AppError> {
    output::prepare(context, &context.base_dir, workspace.root(), false)?;
    let mut plans = Vec::with_capacity(ids.len());
    let mut prepared = HashMap::new();
    for id in ids {
        let mut row = ExportRow {
            id,
            name: String::new(),
            filename: None,
            output_dir: None,
            output_path: None,
            issues: Vec::new(),
            existing: false,
            stamp: None,
        };
        if cancelled.load(Ordering::Acquire) {
            plans.push((None, row));
            continue;
        }
        let package = workspace.package(&row.id).and_then(|package| {
            let root = workspace.package_dir(&row.id)?;
            let preview = package::preview_cancellable(&package, &root, daz_folders, cancelled);
            row.name = package.name.clone();
            row.filename = preview.filename;
            row.issues = preview.issues;
            let destination = output::resolve(context, &package);
            row.output_dir = Some(destination.clone());
            row.output_path = row.filename.as_ref().map(|name| destination.join(name));
            let prepared = prepared
                .entry(paths::path_key(&destination))
                .or_insert_with(|| {
                    output::prepare(context, &destination, workspace.root(), create_directories)
                });
            if let Err(error) = prepared {
                row.error("invalidOutputDirectory", error.clone());
            } else if let Some(path) = &row.output_path {
                match package::output_stamp(path) {
                    Ok(stamp) => {
                        row.existing = stamp.is_some();
                        row.stamp = stamp;
                    }
                    Err(error) => row.error("invalidOutputFile", error),
                }
            }
            Ok(package)
        });
        match package {
            Ok(package) => plans.push((Some(package), row)),
            Err(error) => {
                row.error("packageUnavailable", error);
                plans.push((None, row));
            }
        }
    }
    mark_collisions(&mut plans);
    Ok(plans)
}

pub fn execute(
    workspace: &Workspace,
    daz_folders: &[String],
    context: &OutputContext,
    plans: Vec<(Option<Package>, ExportRow)>,
    approved: &[Replacement],
    cancelled: &AtomicBool,
    progress: impl Fn(&str, Phase, u64, u64),
) -> Vec<ExportResult> {
    plans
        .into_iter()
        .map(|(package, row)| {
            let replacement = approved.iter().find(|item| item.id == row.id);
            let output_changed = replacement.is_some_and(|item| {
                !row.output_path
                    .as_ref()
                    .is_some_and(|path| paths::path_key(path) == paths::path_key(&item.output_path))
                    || Some(&item.stamp) != row.stamp.as_ref()
            });
            let (status, path, message) = if cancelled.load(Ordering::Acquire) {
                (ExportStatus::Skipped, None, None)
            } else if output_changed {
                (ExportStatus::Failed, None, Some("outputChanged".into()))
            } else if let Some(package) =
                package.filter(|_| row.ready() && (!row.existing || replacement.is_some()))
            {
                let result = workspace.package_dir(&row.id).and_then(|root| {
                    let destination = row.output_dir.as_deref().ok_or("invalidOutputDirectory")?;
                    output::prepare(context, destination, workspace.root(), false)?;
                    package::export(
                        &package,
                        &root,
                        daz_folders,
                        destination,
                        replacement.map(|item| item.stamp.as_str()),
                        cancelled,
                        |phase, done, total| {
                            progress(&row.id, phase, done, total);
                        },
                    )
                });
                match result {
                    Ok(path) => (ExportStatus::Success, Some(path), None),
                    Err(_) if cancelled.load(Ordering::Acquire) => {
                        (ExportStatus::Cancelled, None, None)
                    }
                    Err(message) => (ExportStatus::Failed, None, Some(message)),
                }
            } else {
                let reason = row
                    .issues
                    .iter()
                    .find(|issue| issue.severity == Severity::Error)
                    .map_or_else(
                        || AppError::new("exportBlocked"),
                        |issue| issue.error.clone(),
                    );
                (ExportStatus::Skipped, None, Some(reason))
            };
            ExportResult {
                id: row.id,
                status,
                path,
                message,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, path::Path};

    fn preflight(
        workspace: &Workspace,
        ids: Vec<String>,
        destination: &Path,
    ) -> Vec<(Option<Package>, ExportRow)> {
        plan(
            workspace,
            &workspace.settings().unwrap().daz_folders,
            ids,
            &context(workspace, destination),
            true,
            &AtomicBool::new(false),
        )
        .unwrap()
    }

    fn context(workspace: &Workspace, destination: &Path) -> OutputContext {
        OutputContext::today(
            destination.to_owned(),
            workspace.settings().unwrap().output_organization,
        )
    }

    fn product(workspace: &Workspace, name: &str, sku: &str) -> Package {
        let mut package = workspace.create(name).unwrap();
        package.sku = sku.into();
        workspace.save(&package).unwrap();
        let content = workspace
            .package_dir(&package.id)
            .unwrap()
            .join("Content/People");
        fs::create_dir(&content).unwrap();
        fs::write(content.join("test.duf"), "fixture").unwrap();
        package
    }

    #[test]
    fn dated_batch_uses_the_local_day_beneath_the_base() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let destination = temporary.path().join("export");
        fs::create_dir(&destination).unwrap();
        let package = product(&workspace, "Dated", "1");
        let mut settings = workspace.settings().unwrap();
        settings.output_organization = dim_creator_core::model::OutputOrganization::ByDate;
        workspace.save_settings(&settings).unwrap();
        let plans = preflight(&workspace, vec![package.id], &destination);
        let results = execute(
            &workspace,
            &settings.daz_folders,
            &context(&workspace, &destination),
            plans,
            &[],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert_eq!(results[0].status, ExportStatus::Success);
        let path = results[0].path.as_ref().unwrap();
        assert_ne!(path.parent().unwrap(), destination);
        assert_eq!(path.parent().unwrap().parent().unwrap(), destination);
    }

    #[test]
    fn batch_exports_valid_packages_and_skips_collisions_and_missing_metadata() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let destination = temporary.path().join("export");
        fs::create_dir(&destination).unwrap();
        let valid = product(&workspace, "Valid", "1");
        let missing = product(&workspace, "Missing", "");
        let collision = product(&workspace, "Duplicated", "2");
        let duplicate = product(&workspace, "duplicated", "2");
        let plans = preflight(
            &workspace,
            vec![
                valid.id,
                missing.id,
                collision.id,
                duplicate.id,
                uuid::Uuid::new_v4().to_string(),
            ],
            &destination,
        );
        assert_eq!(
            plans.iter().map(|(_, row)| row.ready()).collect::<Vec<_>>(),
            vec![true, false, false, false, false]
        );
        assert!(
            plans[2]
                .1
                .issues
                .iter()
                .any(|issue| issue.error.code == "duplicateOutput")
        );
        let results = execute(
            &workspace,
            &workspace.settings().unwrap().daz_folders,
            &context(&workspace, &destination),
            plans,
            &[],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert_eq!(
            results
                .iter()
                .map(|result| result.status)
                .collect::<Vec<_>>(),
            vec![
                ExportStatus::Success,
                ExportStatus::Skipped,
                ExportStatus::Skipped,
                ExportStatus::Skipped,
                ExportStatus::Skipped
            ]
        );
        assert!(results[0].path.as_ref().unwrap().is_file());
        assert_eq!(fs::read_dir(destination).unwrap().count(), 1);
    }

    #[test]
    fn an_existing_archive_requires_approval() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let destination = temporary.path().join("export");
        fs::create_dir(&destination).unwrap();
        let package = product(&workspace, "Valid", "1");
        let first_plan = preflight(&workspace, vec![package.id.clone()], &destination);
        let target = destination.join(first_plan[0].1.filename.as_ref().unwrap());
        fs::write(&target, b"old zip").unwrap();
        let existing_plan = preflight(&workspace, vec![package.id.clone()], &destination);
        assert!(existing_plan[0].1.existing);
        let results = execute(
            &workspace,
            &workspace.settings().unwrap().daz_folders,
            &context(&workspace, &destination),
            existing_plan,
            &[],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert_eq!(results[0].status, ExportStatus::Skipped);
        assert_eq!(fs::read(&target).unwrap(), b"old zip");

        let approved = Replacement {
            id: package.id.clone(),
            output_path: target.clone(),
            stamp: package::output_stamp(&target).unwrap().unwrap(),
        };
        fs::write(&target, b"externally modified zip").unwrap();
        let updated = preflight(&workspace, vec![package.id], &destination);
        let results = execute(
            &workspace,
            &workspace.settings().unwrap().daz_folders,
            &context(&workspace, &destination),
            updated,
            &[approved],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert_eq!(
            results[0].message.as_ref().map(|error| error.code.as_str()),
            Some("outputChanged")
        );
        assert_eq!(fs::read(target).unwrap(), b"externally modified zip");
    }

    #[test]
    fn batch_uses_one_folder_snapshot_and_new_preflight_uses_saved_settings() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let destination = temporary.path().join("export");
        fs::create_dir(&destination).unwrap();
        let first = product(&workspace, "First", "1");
        let second = product(&workspace, "Second", "2");
        let ids = vec![first.id, second.id];
        let mut settings = workspace.settings().unwrap();
        let snapshot = settings.daz_folders.clone();
        let plans = preflight(&workspace, ids.clone(), &destination);
        settings.daz_folders.clear();
        workspace.save_settings(&settings).unwrap();
        let results = execute(
            &workspace,
            &snapshot,
            &context(&workspace, &destination),
            plans,
            &[],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert!(
            results
                .iter()
                .all(|result| result.status == ExportStatus::Success)
        );
        assert!(
            preflight(&workspace, ids, &destination)
                .iter()
                .all(|(_, row)| !row.ready())
        );
    }

    #[test]
    fn output_directory_cannot_be_the_workspace_or_a_child() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = temporary.path().join("workspace");
        let child = workspace.join("packages");
        fs::create_dir_all(&child).unwrap();
        assert!(output::validate_directory(&workspace, &workspace).is_err());
        assert!(output::validate_directory(&child, &workspace).is_err());
        assert!(output::validate_directory(temporary.path(), &workspace).is_ok());
    }

    #[test]
    fn package_targets_control_collisions_execution_and_replacement_approvals() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let base = temporary.path().join("export");
        fs::create_dir(&base).unwrap();
        let first = product(&workspace, "Same", "1");
        let second = product(&workspace, "Same", "1");
        let context = context(&workspace, &base);
        // Synthetic per-package targets exercise the executor without adding a future mode.
        let mut plans = [first, second]
            .into_iter()
            .enumerate()
            .map(|(index, package)| {
                let mut row = preflight(&workspace, vec![package.id.clone()], &base)
                    .remove(0)
                    .1;
                let directory = base.join(index.to_string());
                output::prepare(&context, &directory, workspace.root(), true).unwrap();
                row.output_path = Some(directory.join(row.filename.as_ref().unwrap()));
                row.output_dir = Some(directory);
                (Some(package), row)
            })
            .collect::<Vec<_>>();
        mark_collisions(&mut plans);
        assert!(plans.iter().all(|(_, row)| row.ready()));
        let targets = plans
            .iter()
            .map(|(_, row)| row.output_path.clone().unwrap())
            .collect::<Vec<_>>();
        let results = execute(
            &workspace,
            &workspace.settings().unwrap().daz_folders,
            &context,
            plans,
            &[],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert!(
            results
                .iter()
                .all(|result| result.status == ExportStatus::Success)
        );
        assert!(targets.iter().all(|path| path.is_file()));

        let package = product(&workspace, "Replacement", "3");
        let mut plans = preflight(&workspace, vec![package.id.clone()], &base);
        let path = plans[0].1.output_path.clone().unwrap();
        fs::write(&path, "original").unwrap();
        plans = preflight(&workspace, vec![package.id.clone()], &base);
        let approval = Replacement {
            id: package.id.clone(),
            output_path: base.join("elsewhere").join(path.file_name().unwrap()),
            stamp: plans[0].1.stamp.clone().unwrap(),
        };
        let results = execute(
            &workspace,
            &workspace.settings().unwrap().daz_folders,
            &context,
            plans,
            &[approval],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert_eq!(results[0].message.as_ref().unwrap().code, "outputChanged");
        assert_eq!(fs::read(&path).unwrap(), b"original");

        let approved = Replacement {
            id: package.id.clone(),
            output_path: path.clone(),
            stamp: package::output_stamp(&path).unwrap().unwrap(),
        };
        let plans = preflight(&workspace, vec![package.id], &base);
        let results = execute(
            &workspace,
            &workspace.settings().unwrap().daz_folders,
            &context,
            plans,
            &[approved],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert_eq!(results[0].status, ExportStatus::Success);
        assert_ne!(fs::read(&path).unwrap(), b"original");
    }

    #[test]
    fn confirmed_targets_survive_rollover_but_reject_renamed_packages() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let base = temporary.path().join("export");
        fs::create_dir(&base).unwrap();
        let mut package = product(&workspace, "Before", "1");
        let context = OutputContext::on_date(
            base,
            dim_creator_core::model::OutputOrganization::ByDate,
            chrono::NaiveDate::from_ymd_opt(2025, 12, 31).unwrap(),
        );
        let folders = workspace.settings().unwrap().daz_folders;
        let plans = plan(
            &workspace,
            &folders,
            vec![package.id.clone()],
            &context,
            true,
            &AtomicBool::new(false),
        )
        .unwrap();
        let targets = vec![ExportTarget {
            id: package.id.clone(),
            output_path: plans[0].1.output_path.clone().unwrap(),
        }];
        let restored: OutputContext =
            serde_json::from_value(serde_json::to_value(context).unwrap()).unwrap();
        let mut plans = plan(
            &workspace,
            &folders,
            vec![package.id.clone()],
            &restored,
            false,
            &AtomicBool::new(false),
        )
        .unwrap();
        verify_targets(&mut plans, &targets);
        assert!(plans[0].1.ready());
        assert!(
            plans[0]
                .1
                .output_dir
                .as_ref()
                .unwrap()
                .ends_with("2025-12-31")
        );
        package.name = "After".into();
        workspace.save(&package).unwrap();
        let mut changed = plan(
            &workspace,
            &folders,
            vec![package.id],
            &restored,
            false,
            &AtomicBool::new(false),
        )
        .unwrap();
        verify_targets(&mut changed, &targets);
        let results = execute(
            &workspace,
            &folders,
            &restored,
            changed,
            &[],
            &AtomicBool::new(false),
            |_, _, _, _| {},
        );
        assert_eq!(results[0].status, ExportStatus::Skipped);
        assert_eq!(
            results[0].message.as_ref().unwrap().code,
            "outputTargetChanged"
        );
        assert!(!targets[0].output_path.exists());
    }
}
