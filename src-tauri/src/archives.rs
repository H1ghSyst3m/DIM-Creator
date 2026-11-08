use crate::{Desktop, RunningJob, background};
use dim_creator_core::{
    archives::{ArchiveControl, ArchivePhase, ArchiveProgress, ArchiveSession, ArchiveSessionView},
    error::AppError,
    workspace::{CopyPolicy, ImportDraft, ImportPlan, ImportPreview, ImportResult},
};
use serde::Serialize;
use std::{
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tauri::{State, async_runtime, ipc::Channel};
use uuid::Uuid;

#[derive(Default)]
pub(crate) struct ImportState {
    session: Option<ArchiveSession>,
    plan: Option<ImportPlan>,
    control: Option<ArchiveControl>,
}

#[derive(Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveJobEvent {
    pub job_id: String,
    pub progress: ArchiveProgress,
    pub finished: bool,
    pub session: Option<ArchiveSessionView>,
    pub result: Option<ImportResult>,
    pub error: Option<AppError>,
}

fn reserve(state: &Desktop) -> Result<Arc<RunningJob>, AppError> {
    let _write = state.writes.lock().map_err(|_| "workspaceUnavailable")?;
    let mut slot = state.job.lock().map_err(|_| "workspaceUnavailable")?;
    if slot.is_some() || state.workspace.worklist_save_pending() {
        return Err("exportRunning".into());
    }
    let job = Arc::new(RunningJob {
        id: Uuid::new_v4().to_string(),
        cancelled: AtomicBool::new(false),
    });
    *slot = Some(job.clone());
    Ok(job)
}

fn initial_progress() -> ArchiveProgress {
    ArchiveProgress {
        phase: ArchivePhase::Starting,
        name: String::new(),
        entries: 0,
        bytes: 0,
    }
}

fn monitor(
    job: Arc<RunningJob>,
    control: ArchiveControl,
    progress: Arc<Mutex<ArchiveProgress>>,
    channel: Channel<ArchiveJobEvent>,
    done: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        while !done.load(Ordering::Acquire) {
            if job.cancelled.load(Ordering::Relaxed) {
                control.cancel();
            }
            let _ = control.check(); // The deadline also reaches RAR's decoder cancellation token.
            if let Ok(current) = progress.lock() {
                let mut value = current.clone();
                value.entries = control.entries();
                value.bytes = control.processed_bytes();
                value.phase = control.phase();
                let _ = channel.send(ArchiveJobEvent {
                    job_id: job.id.clone(),
                    progress: value,
                    finished: false,
                    session: None,
                    result: None,
                    error: None,
                });
            }
            std::thread::sleep(Duration::from_millis(200));
        }
    })
}

#[tauri::command]
pub async fn analyze_archives(
    state: State<'_, Arc<Desktop>>,
    sources: Vec<PathBuf>,
    on_event: Channel<ArchiveJobEvent>,
) -> Result<String, AppError> {
    if sources.is_empty() || sources.len() > 100 {
        return Err("archiveLimit".into());
    }
    let state = Arc::clone(state.inner());
    let job = reserve(&state)?;
    let control = ArchiveControl::default();
    {
        let mut imports = state.imports.lock().map_err(|_| "workspaceUnavailable")?;
        imports.session = None;
        imports.plan = None;
        imports.control = Some(control.clone());
    }
    let id = job.id.clone();
    async_runtime::spawn(async move {
        let progress = Arc::new(Mutex::new(initial_progress()));
        let done = Arc::new(AtomicBool::new(false));
        let monitor = monitor(
            job.clone(),
            control.clone(),
            progress.clone(),
            on_event.clone(),
            done.clone(),
        );
        let worker_state = state.clone();
        let result = background(move || {
            let settings = worker_state.workspace.settings()?;
            ArchiveSession::analyze(
                &sources,
                worker_state.workspace.root(),
                &settings,
                &control,
                |value| {
                    if let Ok(mut progress) = progress.lock() {
                        *progress = value;
                    }
                },
            )
        })
        .await;
        done.store(true, Ordering::Release);
        let _ = monitor.join();
        let (session, error) = match result {
            Ok(session) => {
                let view = session.view();
                match state.imports.lock() {
                    Ok(mut slot) => {
                        slot.session = Some(session);
                        slot.control = None;
                        (Some(view), None)
                    }
                    Err(_) => (None, Some("workspaceUnavailable".into())),
                }
            }
            Err(error) => (None, Some(error)),
        };
        if let Ok(mut slot) = state.job.lock() {
            *slot = None;
        }
        let _ = on_event.send(ArchiveJobEvent {
            job_id: job.id.clone(),
            progress: initial_progress(),
            finished: true,
            session,
            result: None,
            error,
        });
    });
    Ok(id)
}

#[tauri::command]
pub async fn preview_archive_import(
    state: State<'_, Arc<Desktop>>,
    session_id: String,
    draft: ImportDraft,
) -> Result<ImportPreview, AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|workspace| {
            let mut slot = state.imports.lock().map_err(|_| "workspaceUnavailable")?;
            let session = slot
                .session
                .as_ref()
                .filter(|session| session.id() == session_id)
                .ok_or("archiveSourceChanged")?;
            let plan = workspace.plan_import(session, &draft)?;
            let view = plan.view.clone();
            slot.plan = Some(plan);
            Ok(view)
        })
    })
    .await
}

#[tauri::command]
pub async fn start_archive_import(
    state: State<'_, Arc<Desktop>>,
    session_id: String,
    plan_id: String,
    policy: CopyPolicy,
    on_event: Channel<ArchiveJobEvent>,
) -> Result<String, AppError> {
    let state = Arc::clone(state.inner());
    let job = reserve(&state)?;
    let control = ArchiveControl::default();
    let prepared = (|| {
        let mut slot = state.imports.lock().map_err(|_| "workspaceUnavailable")?;
        if !slot
            .session
            .as_ref()
            .is_some_and(|session| session.id() == session_id)
            || !slot
                .plan
                .as_ref()
                .is_some_and(|plan| plan.view.id == plan_id)
        {
            return Err(AppError::new("archiveSourceChanged"));
        }
        slot.control = Some(control.clone());
        Ok((slot.session.take().unwrap(), slot.plan.take().unwrap()))
    })();
    let (session, plan) = match prepared {
        Ok(value) => value,
        Err(error) => {
            *state.job.lock().map_err(|_| "workspaceUnavailable")? = None;
            return Err(error);
        }
    };
    let id = job.id.clone();
    async_runtime::spawn(async move {
        let progress = Arc::new(Mutex::new(initial_progress()));
        let done = Arc::new(AtomicBool::new(false));
        let monitor = monitor(
            job.clone(),
            control.clone(),
            progress.clone(),
            on_event.clone(),
            done.clone(),
        );
        let worker_state = state.clone();
        let result = background(move || {
            worker_state
                .workspace
                .apply_import(&session, plan, policy, &control, |target| {
                    if let Ok(mut current) = progress.lock() {
                        current.phase = ArchivePhase::Import;
                        current.name = target.into();
                    }
                })
        })
        .await;
        done.store(true, Ordering::Release);
        let _ = monitor.join();
        if let Ok(mut slot) = state.imports.lock() {
            slot.control = None;
        }
        if let Ok(mut slot) = state.job.lock() {
            *slot = None;
        }
        let (result, error) = match result {
            Ok(result) => (Some(result), None),
            Err(error) => (None, Some(error)),
        };
        let _ = on_event.send(ArchiveJobEvent {
            job_id: job.id.clone(),
            progress: initial_progress(),
            finished: true,
            session: None,
            result,
            error,
        });
    });
    Ok(id)
}

#[tauri::command]
pub fn cancel_archive_job(state: State<'_, Arc<Desktop>>, job_id: String) -> Result<(), AppError> {
    let slot = state.job.lock().map_err(|_| "workspaceUnavailable")?;
    if let Some(job) = slot.as_ref().filter(|job| job.id == job_id) {
        job.cancelled.store(true, Ordering::Release);
        if let Some(control) = &state
            .imports
            .lock()
            .map_err(|_| "workspaceUnavailable")?
            .control
        {
            control.cancel();
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn release_archive_session(
    state: State<'_, Arc<Desktop>>,
    session_id: String,
) -> Result<(), AppError> {
    let state = Arc::clone(state.inner());
    background(move || {
        state.mutate(|_| {
            let mut slot = state.imports.lock().map_err(|_| "workspaceUnavailable")?;
            if slot
                .session
                .as_ref()
                .is_some_and(|session| session.id() == session_id)
            {
                *slot = ImportState::default();
            }
            Ok(())
        })
    })
    .await
}
