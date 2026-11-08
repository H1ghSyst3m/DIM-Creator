//! Read-only destination enrichment of the core synchronization plan.
use crate::output::{self, OutputContext, OutputDirectory};
use dim_creator_core::{
    error::{AppError, Issue, Severity},
    filesystem, package,
    synchronization::{SyncChange, SyncGroup, SyncRequest},
    workspace::{SyncPlan, Workspace, fingerprint},
};
use serde::Serialize;
use std::{collections::BTreeMap, path::Path};

#[derive(Serialize, ts_rs::TS)]
#[serde(tag = "status", rename_all = "camelCase")]
pub enum SyncLoad {
    Ready {
        session: Box<SyncSession>,
    },
    Unavailable {
        worklist: Box<dim_creator_core::worklist::WorklistState>,
        error: AppError,
    },
}

#[derive(Serialize, ts_rs::TS)]
pub struct SyncSession {
    pub worklist: dim_creator_core::worklist::WorklistState,
    pub data: SyncGroup,
    pub context: OutputContext,
    pub metadata: Vec<SyncMetadata>,
    pub outputs: Vec<OutputDirectory>,
}

#[derive(Serialize, ts_rs::TS)]
pub struct SyncMetadata {
    pub id: String,
    pub preview: package::Preview,
}

#[derive(Serialize, ts_rs::TS)]
pub struct SyncNotice {
    pub id: String,
    pub issues: Vec<Issue>,
}

#[derive(Serialize, ts_rs::TS)]
pub struct SyncPreview {
    pub changes: Vec<SyncChange>,
    pub order: Option<Vec<String>>,
    pub stamp: String,
    pub metadata: Vec<SyncMetadata>,
    pub outputs: Vec<OutputDirectory>,
    pub notices: Vec<SyncNotice>,
}

pub fn load(workspace: &Workspace, group: &str) -> Result<SyncLoad, AppError> {
    let settings = workspace.settings()?;
    let worklist = workspace.reload_sync_worklist()?;
    let data = match workspace.load_sync(group) {
        Ok(data) => data,
        Err(error) => {
            return Ok(SyncLoad::Unavailable {
                worklist: Box::new(worklist),
                error,
            });
        }
    };
    let context = OutputContext::today(
        settings.output_dir.unwrap_or_default().into(),
        settings.output_organization,
    );
    let metadata = data
        .packages
        .iter()
        .map(|package| SyncMetadata {
            id: package.id.clone(),
            preview: package::metadata_preview(package),
        })
        .collect();
    let outputs = if context.validate(workspace.root()).is_ok() {
        data.packages
            .iter()
            .map(|package| {
                let directory = output::resolve(&context, package);
                OutputDirectory {
                    id: package.id.clone(),
                    key: filesystem::path_key(&directory),
                    directory,
                }
            })
            .collect()
    } else {
        vec![]
    };
    Ok(SyncLoad::Ready {
        session: Box::new(SyncSession {
            worklist,
            data,
            context,
            metadata,
            outputs,
        }),
    })
}

pub fn preview(
    workspace: &Workspace,
    request: &SyncRequest,
    context: &OutputContext,
) -> Result<(SyncPlan, SyncPreview), AppError> {
    let settings = workspace.settings()?;
    if Path::new(settings.output_dir.as_deref().unwrap_or_default()) != context.base_dir
        || settings.output_organization != context.organization
    {
        return Err("syncStale".into());
    }
    let date = chrono::NaiveDate::parse_from_str(&context.local_date, "%Y-%m-%d")
        .map_err(|_| AppError::new("invalidOutputContext"))?;
    if date.to_string() != context.local_date {
        return Err("invalidOutputContext".into());
    }
    let plan = workspace.plan_sync(request)?;
    let planned = plan.packages();
    let (mut catalog, warnings) = workspace.catalog()?;
    catalog.sort_by(|a, b| a.id.cmp(&b.id));
    // Stamp the original metadata; generated cover names deliberately do not form part of it.
    let catalog_stamp = fingerprint(&catalog)?;
    for package in &mut catalog {
        if let Some(next) = planned.iter().find(|p| p.id == package.id) {
            *package = next.clone();
        }
    }
    let configured = settings.output_dir.is_some();
    let base_error = if configured {
        context.validate(workspace.root()).err()
    } else {
        None
    };
    let mut destinations: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for package in &catalog {
        if let Some(filename) = package::metadata_preview(package).filename {
            let target = output::resolve(context, package).join(filename);
            destinations
                .entry(filesystem::path_key(&target))
                .or_default()
                .push(package.id.clone());
        }
    }
    let mut notices = Vec::new();
    let mut outputs = Vec::new();
    let mut metadata = Vec::new();
    let mut target_stamps = Vec::new();
    for package in &planned {
        let meta = package::metadata_preview(package);
        let directory = output::resolve(context, package);
        let mut issues = meta.issues.clone();
        if let Some(error) = &base_error {
            issues.push(Issue::cause(
                "invalidOutputDirectory",
                "output",
                error.clone(),
            ));
        }
        if let Some(filename) = &meta.filename {
            let target = directory.join(filename);
            if destinations
                .get(&filesystem::path_key(&target))
                .is_some_and(|ids| ids.len() > 1)
            {
                issues.push(Issue::value("duplicateOutput", "output", target.display()));
            }
            if configured && base_error.is_none() {
                let result = (|| {
                    // Missing date directories are valid previews; existing ones must be safe.
                    match std::fs::symlink_metadata(&directory) {
                        Ok(_) => output::validate_directory(&directory, workspace.root())?,
                        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
                        Err(e) => return Err(AppError::diagnostic("ioError", e)),
                    }
                    package::output_stamp(&target)
                })();
                match &result {
                    Ok(Some(_)) => issues.push(Issue {
                        error: AppError::new("syncExistingOutput").param("path", target.display()),
                        severity: Severity::Warning,
                        field: "output".into(),
                    }),
                    Err(error) => {
                        issues.push(Issue::cause("invalidOutputFile", "output", error.clone()))
                    }
                    _ => {}
                }
                target_stamps.push((target, result));
            }
        }
        if configured && base_error.is_none() {
            outputs.push(OutputDirectory {
                id: package.id.clone(),
                key: filesystem::path_key(&directory),
                directory,
            });
        }
        notices.push(SyncNotice {
            id: package.id.clone(),
            issues,
        });
        metadata.push(SyncMetadata {
            id: package.id.clone(),
            preview: meta,
        });
    }
    if !warnings.is_empty() {
        notices.push(SyncNotice {
            id: String::new(),
            issues: warnings
                .into_iter()
                .map(|error| Issue {
                    error,
                    severity: Severity::Warning,
                    field: "output".into(),
                })
                .collect(),
        });
    }
    let stamp = fingerprint(&(
        &plan.stamp,
        context,
        catalog_stamp,
        target_stamps,
        &base_error,
    ))?;
    let view = SyncPreview {
        changes: plan.changes.clone(),
        order: plan.order.clone(),
        stamp,
        metadata,
        outputs,
        notices,
    };
    // The caller applies the independently recalculated core plan, never frontend package data.
    Ok((plan, view))
}

#[cfg(test)]
mod tests {
    use super::*;
    use dim_creator_core::{
        model::OutputOrganization, synchronization::SyncField, worklist::WorklistAction,
    };

    fn fixture() -> (tempfile::TempDir, Workspace, String) {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::new(dir.path().join("workspace")).unwrap();
        let ids = ["Source", "Target"].map(|name| {
            let mut p = ws.create_listed(name).unwrap().package;
            p.sku = "123".into();
            ws.save(&p).unwrap();
            std::fs::remove_dir(ws.package_dir(&p.id).unwrap().join("Content")).unwrap();
            p.id
        });
        let state = ws
            .change_worklist(
                ws.worklist().unwrap().value.revision,
                &WorklistAction::Group { ids: ids.to_vec() },
            )
            .unwrap();
        let group = state.value.entries[0].id().to_owned();
        (dir, ws, group)
    }

    #[test]
    fn preview_reports_conflicts_without_content_scans_or_creating_output_folders() {
        let (dir, ws, group) = fixture();
        let base = dir.path().join("output");
        std::fs::create_dir(&base).unwrap();
        let mut settings = ws.settings().unwrap();
        settings.output_dir = Some(base.to_string_lossy().into());
        settings.output_organization = OutputOrganization::ByDate;
        ws.save_settings(&settings).unwrap();
        let SyncLoad::Ready { session } = load(&ws, &group).unwrap() else {
            panic!("load")
        };
        let request = SyncRequest {
            group,
            source: session.data.packages[0].id.clone(),
            targets: vec![session.data.packages[1].id.clone()],
            fields: vec![SyncField::Name],
            order: None,
            expected: session.data.expected,
        };
        let mut context = session.context;
        context.local_date = "2025-12-31".into();
        let (plan, view) = preview(&ws, &request, &context).unwrap();
        assert!(view.notices.iter().all(|notice| {
            notice
                .issues
                .iter()
                .any(|issue| issue.error.code == "duplicateOutput")
        }));
        assert!(
            view.metadata
                .iter()
                .all(|row| row.preview.entries.is_empty())
        );
        assert!(!base.join("2025-12-31").exists());
        let result = ws.apply_sync_plan(plan).unwrap();
        assert!(result.error.is_none());
        assert!(!base.join("2025-12-31").exists());
        assert!(result.packages.iter().all(|p| p.name == "Source"));
    }

    #[test]
    fn missing_group_reload_returns_authoritative_structure() {
        let (dir, ws, group) = fixture();
        let external = Workspace::new(dir.path().join("workspace")).unwrap();
        let state = external
            .change_worklist(
                external.worklist().unwrap().value.revision,
                &WorklistAction::Dissolve {
                    group: group.clone(),
                },
            )
            .unwrap();
        let SyncLoad::Unavailable { worklist, error } = load(&ws, &group).unwrap() else {
            panic!("missing group")
        };
        assert_eq!(worklist.value, state.value);
        assert_eq!(error.code, "invalidSyncAction");
        assert!(
            ws.change_worklist(
                worklist.value.revision,
                &WorklistAction::Group {
                    ids: worklist.value.ids()
                }
            )
            .is_ok()
        );
    }
}
