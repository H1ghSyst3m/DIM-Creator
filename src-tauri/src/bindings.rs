//! Explicit generation; the normal check never rewrites source files.
use crate::archives::ArchiveJobEvent;
use crate::{WorkspaceView, jobs::*, output::*, synchronization::*, updates::Update};
use dim_creator_core::archives::{
    ArchiveClassification, ArchiveContent, ArchivePhase, ArchiveProgress, ArchiveRoot,
    ArchiveSessionView, ImportAction,
};
use dim_creator_core::{
    error::*, model::*, package::*, synchronization::*, worklist::*, workspace::*,
};
use ts_rs::{Config, TS};

fn declarations() -> String {
    // Tauri serializes integer byte counts as JSON numbers, not JavaScript bigint.
    let cfg = Config::new().with_large_int("number");
    let mut text =
        String::from("// Generated from Rust by npm run bindings:generate. Do not edit.\n");
    macro_rules! declare { ($($ty:ty),+ $(,)?) => { $(
        text.push_str("export ");
        text.push_str(&<$ty>::decl(&cfg));
        text.push('\n');
    )+ }; }
    declare!(
        ArchiveRoot,
        ArchiveClassification,
        ImportAction,
        ArchiveContent,
        ArchiveSessionView,
        ArchivePhase,
        ArchiveProgress,
        ArchiveJobEvent,
        ImportAssignment,
        ImportTarget,
        ImportGroup,
        ImportGroupPreview,
        ImportDraft,
        ImportTargetPreview,
        ImportPreview,
        ImportTargetResult,
        ImportResult,
        TemplatePreview,
        TemplateResult,
        TemplateSkipReason,
        SyncField,
        SyncExpected,
        SyncGroup,
        SyncRequest,
        SyncChange,
        SyncFailure,
        SyncResult,
        SyncLoad,
        SyncSession,
        SyncMetadata,
        SyncNotice,
        SyncPreview,
        AppError,
        Severity,
        Issue,
        Package,
        WorklistEntry,
        Worklist,
        WorklistState,
        WorklistSource,
        WorklistTarget,
        WorklistAction,
        CreatedPackage,
        Store,
        OutputOrganization,
        Settings,
        SettingsIssue,
        FileEntry,
        CopyPolicy,
        CopyConflict,
        TransferMode,
        FileChangeKind,
        FileStatus,
        FileChange,
        FileResult,
        FileMutation,
        Preview,
        Phase,
        OutputContext,
        OutputDirectory,
        ExportPreflight,
        ExportTarget,
        ExportRow,
        Replacement,
        ExportStatus,
        ExportResult,
        JobEvent,
        Update,
        WorkspaceView
    );
    text
}

fn destination() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../src/bindings.ts")
}

#[test]
fn bindings_are_current() {
    let actual = std::fs::read_to_string(destination()).unwrap_or_default();
    assert_eq!(
        actual.replace("\r\n", "\n"),
        declarations(),
        "Run npm run bindings:generate"
    );
}

#[test]
#[ignore = "explicit generation only"]
fn generate_bindings() {
    std::fs::write(destination(), declarations()).unwrap();
}

#[test]
fn serialized_events_preserve_wire_values_nulls_and_numbers() {
    let event = JobEvent {
        job_id: "job".into(),
        package_id: None,
        phase: Phase::Writing,
        done: 4_294_967_296,
        total: 8_589_934_592,
        results: None,
    };
    let json = serde_json::to_value(event).unwrap();
    assert_eq!(json["phase"], "writing");
    assert_eq!(json["jobId"], "job");
    assert_eq!(json["done"], 4_294_967_296_u64);
    assert!(json["packageId"].is_null());
    assert!(json["results"].is_null());
    let error = AppError::new("reservedNativeName").param("name", "CON .txt");
    let json = serde_json::to_value(Issue::cause("unsafeContent", "content", error)).unwrap();
    assert_eq!(json["code"], "unsafeContent");
    assert_eq!(json["severity"], "error");
    assert_eq!(json["causes"][0]["params"]["name"], "CON .txt");
}

#[test]
fn output_context_and_review_targets_preserve_wire_contracts() {
    let context = OutputContext::on_date(
        "C:/Output".into(),
        OutputOrganization::ByDate,
        chrono::NaiveDate::from_ymd_opt(2026, 1, 9).unwrap(),
    );
    let preflight = ExportPreflight {
        context,
        rows: vec![ExportRow {
            id: "package".into(),
            name: "Test".into(),
            filename: Some("test.zip".into()),
            output_dir: Some("C:/Output/2026-01-09".into()),
            output_path: Some("C:/Output/2026-01-09/test.zip".into()),
            issues: vec![],
            existing: false,
            stamp: None,
        }],
    };
    let json = serde_json::to_value(preflight).unwrap();
    assert_eq!(
        json["context"],
        serde_json::json!({"baseDir":"C:/Output","organization":"byDate","localDate":"2026-01-09"})
    );
    assert_eq!(
        json["rows"][0]["outputPath"],
        "C:/Output/2026-01-09/test.zip"
    );
    assert!(json["rows"][0]["stamp"].is_null());
    let target: ExportTarget = serde_json::from_value(
        serde_json::json!({"id":"package","outputPath":"C:/Output/2026-01-09/test.zip"}),
    )
    .unwrap();
    assert_eq!(target.id, "package");
    assert_eq!(
        target.output_path,
        std::path::Path::new("C:/Output/2026-01-09/test.zip")
    );
}
