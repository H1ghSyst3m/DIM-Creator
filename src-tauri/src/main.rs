#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use dim_creator_core::error::AppError;

mod archives;
#[cfg(test)]
mod bindings;
mod jobs;
use archives::{
    analyze_archives, cancel_archive_job, preview_archive_import, release_archive_session,
    start_archive_import,
};
mod output;
mod synchronization;
mod updates;

use std::{
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
};

use base64::{Engine, engine::general_purpose::STANDARD};
use dim_creator_core::{
    model::{Package, Settings},
    package::{self, Preview},
    worklist::{CreatedPackage, WorklistAction, WorklistState},
    workspace::{CopyConflict, CopyPolicy, FileEntry, FileMutation, TransferMode, Workspace},
};
use jobs::{ExportPreflight, ExportTarget, JobEvent, Replacement};
use output::{OutputContext, OutputDirectory};
use serde::Serialize;
use tauri::{Manager, State, async_runtime, ipc::Channel};
use tauri_plugin_opener::OpenerExt;
use uuid::Uuid;

struct RunningJob {
    id: String,
    cancelled: AtomicBool,
}

struct Desktop {
    workspace: Workspace,
    writes: Mutex<()>,
    job: Mutex<Option<Arc<RunningJob>>>,
    imports: Mutex<archives::ImportState>,
}

impl Desktop {
    fn busy(&self) -> bool {
        let Ok(_write) = self.writes.try_lock() else {
            return true;
        };
        self.job.try_lock().map_or(true, |slot| slot.is_some())
            || self.workspace.worklist_save_pending()
    }

    fn mutate<T>(
        &self,
        operation: impl FnOnce(&Workspace) -> Result<T, AppError>,
    ) -> Result<T, AppError> {
        let _write = self.writes.lock().map_err(|_| "workspaceUnavailable")?;
        if self
            .job
            .lock()
            .map_err(|_| "workspaceUnavailable")?
            .is_some()
        {
            return Err("exportRunning".into());
        }
        operation(&self.workspace)
    }
}

async fn background<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, AppError> + Send + 'static,
) -> Result<T, AppError> {
    async_runtime::spawn_blocking(work)
        .await
        .map_err(|error| AppError::diagnostic("operationError", error))?
}

#[derive(Serialize, ts_rs::TS)]
struct WorkspaceView {
    packages: Vec<Package>,
    settings: Settings,
    warnings: Vec<AppError>,
    worklist: WorklistState,
}

#[tauri::command]
fn can_close(state: State<'_, Arc<Desktop>>) -> bool {
    !state.busy()
}

#[tauri::command]
async fn load_workspace(state: State<'_, Arc<Desktop>>) -> Result<WorkspaceView, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            let (packages, warnings) = workspace.catalog()?;
            Ok(WorkspaceView {
                packages,
                settings: workspace.settings()?,
                warnings,
                worklist: workspace.worklist()?,
            })
        })
    })
    .await
}

#[tauri::command]
async fn create_package(
    state: State<'_, Arc<Desktop>>,
    name: String,
) -> Result<CreatedPackage, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.create_listed(&name))).await
}

#[tauri::command]
async fn save_package(state: State<'_, Arc<Desktop>>, package: Package) -> Result<(), AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.save(&package))).await
}

#[tauri::command]
async fn remove_package(
    state: State<'_, Arc<Desktop>>,
    id: String,
) -> Result<WorklistState, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            workspace.remove_listed(&id, |path| {
                trash::delete(path).map_err(|error| AppError::diagnostic("operationError", error))
            })
        })
    })
    .await
}

#[tauri::command]
async fn change_worklist(
    state: State<'_, Arc<Desktop>>,
    revision: u32,
    action: WorklistAction,
) -> Result<WorklistState, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.change_worklist(revision, &action))).await
}

#[tauri::command]
async fn undo_worklist(
    state: State<'_, Arc<Desktop>>,
    revision: u32,
) -> Result<WorklistState, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.undo_worklist(revision))).await
}

#[tauri::command]
async fn retry_worklist(state: State<'_, Arc<Desktop>>) -> Result<WorklistState, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(Workspace::retry_worklist)).await
}

#[tauri::command]
async fn save_settings(
    state: State<'_, Arc<Desktop>>,
    settings: Settings,
) -> Result<Settings, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.save_settings(&settings))).await
}

#[tauri::command]
async fn validate_settings(
    settings: Settings,
) -> Result<Vec<dim_creator_core::model::SettingsIssue>, AppError> {
    background(move || Ok(settings.issues())).await
}

#[tauri::command]
fn validate_entry_name(name: String) -> Option<AppError> {
    dim_creator_core::filesystem::validate_segment(&name).err()
}

#[tauri::command]
async fn list_files(
    state: State<'_, Arc<Desktop>>,
    id: String,
    folder: String,
) -> Result<Vec<FileEntry>, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.workspace.files(&id, &folder)).await
}

#[tauri::command]
async fn create_folder(
    state: State<'_, Arc<Desktop>>,
    id: String,
    folder: String,
    name: String,
) -> Result<(), AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.create_folder(&id, &folder, &name))).await
}

#[tauri::command]
async fn open_folder(
    app: tauri::AppHandle,
    state: State<'_, Arc<Desktop>>,
    id: String,
    folder: String,
) -> Result<(), AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        let path = state.workspace.folder(&id, &folder)?;
        app.opener()
            .open_path(path.to_string_lossy().into_owned(), None::<&str>)
            .map_err(|error| AppError::diagnostic("operationError", error))
    })
    .await
}

#[tauri::command]
async fn copy_conflicts(
    state: State<'_, Arc<Desktop>>,
    id: String,
    folder: String,
    sources: Vec<PathBuf>,
) -> Result<Vec<CopyConflict>, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.workspace.copy_conflicts(&id, &folder, &sources)).await
}

#[tauri::command]
async fn copy_files(
    state: State<'_, Arc<Desktop>>,
    id: String,
    folder: String,
    sources: Vec<PathBuf>,
    policy: CopyPolicy,
    approved: Vec<CopyConflict>,
) -> Result<(), AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| workspace.copy_approved(&id, &folder, &sources, policy, &approved))
    })
    .await
}

#[tauri::command]
async fn transfer_conflicts(
    state: State<'_, Arc<Desktop>>,
    id: String,
    folder: String,
    paths: Vec<String>,
    mode: TransferMode,
) -> Result<Vec<CopyConflict>, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state
            .workspace
            .transfer_conflicts(&id, &folder, &paths, mode)
    })
    .await
}

#[tauri::command]
async fn transfer_files(
    state: State<'_, Arc<Desktop>>,
    id: String,
    folder: String,
    paths: Vec<String>,
    mode: TransferMode,
    policy: CopyPolicy,
    approved: Vec<CopyConflict>,
) -> Result<FileMutation, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            workspace.transfer_files(&id, &folder, &paths, mode, policy, &approved)
        })
    })
    .await
}

#[tauri::command]
async fn rename_entry(
    state: State<'_, Arc<Desktop>>,
    id: String,
    path: String,
    name: String,
) -> Result<FileMutation, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.rename_entry(&id, &path, &name))).await
}

#[tauri::command]
async fn trash_entries(
    state: State<'_, Arc<Desktop>>,
    id: String,
    paths: Vec<String>,
) -> Result<FileMutation, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            workspace.trash_entries(&id, &paths, |path| {
                trash::delete(path).map_err(|error| AppError::diagnostic("operationError", error))
            })
        })
    })
    .await
}

#[tauri::command]
async fn set_cover(
    state: State<'_, Arc<Desktop>>,
    id: String,
    source: Option<PathBuf>,
) -> Result<Package, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| workspace.set_cover(&id, source.as_deref()))).await
}

#[tauri::command]
async fn get_cover(state: State<'_, Arc<Desktop>>, id: String) -> Result<Option<String>, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        Ok(state
            .workspace
            .cover(&id)?
            .map(|bytes| format!("data:image/jpeg;base64,{}", STANDARD.encode(bytes))))
    })
    .await
}

#[tauri::command]
async fn preview_package(state: State<'_, Arc<Desktop>>, id: String) -> Result<Preview, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        Ok(package::preview(
            &state.workspace.package(&id)?,
            &state.workspace.package_dir(&id)?,
            &state.workspace.settings()?.daz_folders,
        ))
    })
    .await
}

#[tauri::command]
async fn preview_outputs(
    state: State<'_, Arc<Desktop>>,
    ids: Vec<String>,
) -> Result<Vec<OutputDirectory>, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        let settings = state.workspace.settings()?;
        let Some(base) = settings.output_dir else {
            return Ok(Vec::new());
        };
        let context = OutputContext::today(base.into(), settings.output_organization);
        context.validate(state.workspace.root())?;
        ids.into_iter()
            .map(|id| {
                let package = state.workspace.package(&id)?;
                let directory = output::resolve(&context, &package);
                let key = dim_creator_core::filesystem::path_key(&directory);
                Ok(OutputDirectory { id, directory, key })
            })
            .collect()
    })
    .await
}

#[tauri::command]
async fn load_group_sync(
    state: State<'_, Arc<Desktop>>,
    group: String,
) -> Result<synchronization::SyncLoad, AppError> {
    let state = Arc::clone(state.inner());
    background(move || state.mutate(|workspace| synchronization::load(workspace, &group))).await
}

#[tauri::command]
async fn preview_group_sync(
    state: State<'_, Arc<Desktop>>,
    request: dim_creator_core::synchronization::SyncRequest,
    context: OutputContext,
) -> Result<synchronization::SyncPreview, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            synchronization::preview(workspace, &request, &context).map(|(_, view)| view)
        })
    })
    .await
}

#[tauri::command]
async fn apply_group_sync(
    state: State<'_, Arc<Desktop>>,
    request: dim_creator_core::synchronization::SyncRequest,
    context: OutputContext,
    stamp: String,
) -> Result<dim_creator_core::workspace::SyncResult, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            let (plan, view) = synchronization::preview(workspace, &request, &context)?;
            if view.stamp != stamp {
                return Err("syncStale".into());
            }
            workspace.apply_sync_plan(plan)
        })
    })
    .await
}

#[tauri::command]
async fn preflight_export(
    state: State<'_, Arc<Desktop>>,
    ids: Vec<String>,
    output_dir: String,
) -> Result<ExportPreflight, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            let settings = workspace.settings()?;
            let context = OutputContext::today(output_dir.into(), settings.output_organization);
            let rows = jobs::plan(
                workspace,
                &settings.daz_folders,
                ids,
                &context,
                true,
                &AtomicBool::new(false),
            )?
            .into_iter()
            .map(|(_, row)| row)
            .collect();
            Ok(ExportPreflight { context, rows })
        })
    })
    .await
}

#[tauri::command]
async fn start_export(
    state: State<'_, Arc<Desktop>>,
    context: OutputContext,
    targets: Vec<ExportTarget>,
    replace: Vec<Replacement>,
    on_event: Channel<JobEvent>,
) -> Result<String, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        if targets.is_empty() {
            return Err("noReadyPackages".into());
        }
        let write = state.writes.lock().map_err(|_| "workspaceUnavailable")?;
        if state
            .job
            .lock()
            .map_err(|_| "workspaceUnavailable")?
            .is_some()
        {
            return Err("exportRunning".into());
        }
        context.validate(state.workspace.root())?;
        let daz_folders = state.workspace.settings()?.daz_folders;
        let id = Uuid::new_v4().to_string();
        let job = Arc::new(RunningJob {
            id: id.clone(),
            cancelled: AtomicBool::new(false),
        });
        *state.job.lock().map_err(|_| "workspaceUnavailable")? = Some(job.clone());
        drop(write);
        async_runtime::spawn_blocking(move || {
            let plans = jobs::plan(
                &state.workspace,
                &daz_folders,
                targets.iter().map(|target| target.id.clone()).collect(),
                &context,
                false,
                &job.cancelled,
            );
            let results = match plans {
                Ok(mut plans) => {
                    jobs::verify_targets(&mut plans, &targets);
                    jobs::execute(
                        &state.workspace,
                        &daz_folders,
                        &context,
                        plans,
                        &replace,
                        &job.cancelled,
                        |package_id, phase, done, total| {
                            let _ = on_event.send(JobEvent {
                                job_id: job.id.clone(),
                                package_id: Some(package_id.into()),
                                phase,
                                done,
                                total,
                                results: None,
                            });
                        },
                    )
                }
                Err(error) => targets
                    .iter()
                    .map(|target| jobs::ExportResult {
                        id: target.id.clone(),
                        status: jobs::ExportStatus::Failed,
                        path: None,
                        message: Some(error.clone()),
                    })
                    .collect(),
            };
            if let Ok(mut slot) = state.job.lock() {
                *slot = None;
            }
            let _ = on_event.send(JobEvent {
                job_id: job.id.clone(),
                package_id: None,
                phase: package::Phase::Finished,
                done: results.len() as u64,
                total: results.len() as u64,
                results: Some(results),
            });
        });
        Ok(id)
    })
    .await
}

#[tauri::command]
fn cancel_export(state: State<'_, Arc<Desktop>>, job_id: String) -> Result<(), AppError> {
    let slot = state.job.lock().map_err(|_| "workspaceUnavailable")?;
    if let Some(job) = slot.as_ref().filter(|job| job.id == job_id) {
        job.cancelled.store(true, Ordering::Release);
    }
    Ok(())
}

#[tauri::command]
async fn check_updates(app: tauri::AppHandle) -> Result<Option<updates::Update>, AppError> {
    updates::check(&app.package_info().version).await
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event
                && window.state::<Arc<Desktop>>().busy()
            {
                api.prevent_close();
            }
        })
        .setup(|app| {
            let root = app.path().app_local_data_dir()?;
            let workspace = Workspace::new(root).map_err(std::io::Error::other)?;
            app.manage(Arc::new(Desktop {
                workspace,
                writes: Mutex::new(()),
                job: Mutex::new(None),
                imports: Mutex::default(),
            }));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            analyze_archives,
            preview_archive_import,
            start_archive_import,
            cancel_archive_job,
            release_archive_session,
            can_close,
            load_workspace,
            create_package,
            save_package,
            remove_package,
            change_worklist,
            undo_worklist,
            retry_worklist,
            load_group_sync,
            preview_group_sync,
            apply_group_sync,
            save_settings,
            validate_settings,
            validate_entry_name,
            list_files,
            create_folder,
            open_folder,
            copy_conflicts,
            copy_files,
            transfer_conflicts,
            transfer_files,
            rename_entry,
            trash_entries,
            set_cover,
            get_cover,
            preview_package,
            preview_outputs,
            preflight_export,
            start_export,
            cancel_export,
            check_updates
        ])
        .build(tauri::generate_context!())
        .expect("could not start DIM Creator")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                let state = app.state::<Arc<Desktop>>();
                if state.busy() {
                    api.prevent_exit();
                }
            }
        });
}

#[cfg(test)]
mod close_tests {
    use super::*;

    #[test]
    fn close_readiness_follows_writes_and_jobs() {
        let temp = tempfile::tempdir().unwrap();
        let desktop = Desktop {
            workspace: Workspace::new(temp.path().to_path_buf()).unwrap(),
            writes: Mutex::new(()),
            job: Mutex::new(None),
            imports: Mutex::default(),
        };
        assert!(!desktop.busy());
        let write = desktop.writes.lock().unwrap();
        assert!(desktop.busy());
        drop(write);
        assert!(!desktop.busy());
        *desktop.job.lock().unwrap() = Some(Arc::new(RunningJob {
            id: "test".into(),
            cancelled: AtomicBool::new(false),
        }));
        assert!(desktop.busy());
        assert_eq!(
            desktop
                .mutate(|workspace| workspace
                    .change_worklist(0, &WorklistAction::Group { ids: vec![] }))
                .unwrap_err()
                .code,
            "exportRunning"
        );
        desktop
            .job
            .lock()
            .unwrap()
            .as_ref()
            .unwrap()
            .cancelled
            .store(true, Ordering::Release);
        assert!(desktop.busy());
        *desktop.job.lock().unwrap() = None;
        assert!(!desktop.busy());
        let slot = desktop.job.lock().unwrap();
        assert!(desktop.busy());
        drop(slot);
        assert!(!desktop.busy());
        desktop.workspace.worklist().unwrap();
        let path = temp.path().join("worklist.json");
        std::fs::remove_file(&path).unwrap();
        std::fs::create_dir(&path).unwrap();
        let created = desktop
            .mutate(|workspace| workspace.create_listed("Pending structure"))
            .unwrap();
        assert!(created.worklist.save_error.is_some());
        assert!(desktop.busy());
        std::fs::remove_dir(&path).unwrap();
        desktop.mutate(Workspace::retry_worklist).unwrap();
        assert!(!desktop.busy());
    }
}
