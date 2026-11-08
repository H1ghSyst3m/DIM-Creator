use chrono::{Local, NaiveDate};
use dim_creator_core::{
    error::AppError,
    filesystem,
    model::{OutputOrganization, Package},
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct OutputContext {
    pub base_dir: PathBuf,
    pub organization: OutputOrganization,
    pub local_date: String,
}

impl OutputContext {
    pub fn today(base_dir: PathBuf, organization: OutputOrganization) -> Self {
        Self::on_date(base_dir, organization, Local::now().date_naive())
    }

    pub fn on_date(base_dir: PathBuf, organization: OutputOrganization, date: NaiveDate) -> Self {
        Self {
            base_dir,
            organization,
            local_date: date.to_string(),
        }
    }

    pub fn validate(&self, workspace: &Path) -> Result<(), AppError> {
        let date = NaiveDate::parse_from_str(&self.local_date, "%Y-%m-%d")
            .map_err(|_| AppError::new("invalidOutputContext"))?;
        if date.to_string() != self.local_date {
            return Err("invalidOutputContext".into());
        }
        validate_directory(&self.base_dir, workspace)
    }
}

#[derive(Serialize, ts_rs::TS)]
pub struct OutputDirectory {
    pub id: String,
    pub directory: PathBuf,
    pub key: String,
}

// Package data belongs to this boundary even though the first two modes share a directory.
pub fn resolve(context: &OutputContext, _package: &Package) -> PathBuf {
    match context.organization {
        OutputOrganization::Flat => context.base_dir.clone(),
        OutputOrganization::ByDate => context.base_dir.join(&context.local_date),
    }
}

fn io_error(path: &Path, error: std::io::Error) -> AppError {
    AppError::diagnostic("ioError", error).param("path", path.display())
}

pub fn validate_directory(path: &Path, workspace: &Path) -> Result<(), AppError> {
    if !path.is_absolute() || !path.is_dir() {
        return Err(AppError::new("invalidOutputDirectory").param("path", path.display()));
    }
    filesystem::check_chain(path)?;
    let resolved = path.canonicalize().map_err(|error| io_error(path, error))?;
    let workspace = workspace
        .canonicalize()
        .map_err(|error| io_error(workspace, error))?;
    if filesystem::path_contains(&workspace, &resolved) {
        return Err("outputInsideWorkspace".into());
    }
    Ok(())
}

pub fn prepare(
    context: &OutputContext,
    directory: &Path,
    workspace: &Path,
    create: bool,
) -> Result<(), AppError> {
    context.validate(workspace)?;
    let relative = directory
        .strip_prefix(&context.base_dir)
        .map_err(|_| AppError::new("invalidOutputDirectory").param("path", directory.display()))?;
    let mut current = context.base_dir.clone();
    for component in relative.components() {
        let Component::Normal(name) = component else {
            return Err("invalidOutputDirectory".into());
        };
        filesystem::validate_segment(name.to_str().ok_or("invalidOutputDirectory")?)?;
        current.push(name);
        match fs::symlink_metadata(&current) {
            Err(error) if create && error.kind() == std::io::ErrorKind::NotFound => {
                match fs::create_dir(&current) {
                    Ok(()) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                    Err(error) => return Err(io_error(&current, error)),
                }
            }
            Err(error) => return Err(io_error(&current, error)),
            Ok(_) => {}
        }
        validate_directory(&current, workspace)?;
    }
    validate_directory(directory, workspace)?;
    let base = context
        .base_dir
        .canonicalize()
        .map_err(|error| io_error(&context.base_dir, error))?;
    let resolved = directory
        .canonicalize()
        .map_err(|error| io_error(directory, error))?;
    if !filesystem::path_contains(&base, &resolved) {
        return Err("invalidOutputDirectory".into());
    }
    tempfile::Builder::new()
        .prefix(".dim-write-test-")
        .tempfile_in(directory)
        .map(|_| ())
        .map_err(|error| io_error(directory, error))
}

#[cfg(test)]
mod tests {
    use super::*;
    use dim_creator_core::workspace::Workspace;

    #[test]
    fn resolution_is_read_only_and_uses_one_explicit_local_date() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let base = temporary.path().join("export");
        fs::create_dir(&base).unwrap();
        let package = workspace.create("Test").unwrap();
        let mut context = OutputContext::on_date(
            base.clone(),
            OutputOrganization::ByDate,
            NaiveDate::from_ymd_opt(2026, 1, 9).unwrap(),
        );
        let target = resolve(&context, &package);
        assert_eq!(target, base.join("2026-01-09"));
        assert!(!target.exists());
        prepare(&context, &target, workspace.root(), true).unwrap();
        prepare(&context, &target, workspace.root(), false).unwrap();
        assert_eq!(resolve(&context, &package), target);
        context.organization = OutputOrganization::Flat;
        assert_eq!(resolve(&context, &package), base);
    }

    #[test]
    fn rejects_invalid_dates_files_and_targets_outside_the_base() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let base = temporary.path().join("export");
        fs::create_dir(&base).unwrap();
        let package = workspace.create("Test").unwrap();
        let mut context = OutputContext::on_date(
            base.clone(),
            OutputOrganization::ByDate,
            NaiveDate::from_ymd_opt(2026, 1, 9).unwrap(),
        );
        let target = resolve(&context, &package);
        fs::write(&target, "keep").unwrap();
        assert!(prepare(&context, &target, workspace.root(), true).is_err());
        assert_eq!(fs::read(&target).unwrap(), b"keep");
        assert!(prepare(&context, temporary.path(), workspace.root(), true).is_err());
        assert!(prepare(&context, &base.join("../escape"), workspace.root(), true).is_err());
        context.local_date = "../../escape".into();
        assert!(context.validate(workspace.root()).is_err());
        assert!(!temporary.path().join("escape").exists());
    }

    #[cfg(windows)]
    #[test]
    fn rejects_a_junction_without_writing_through_it() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = Workspace::new(temporary.path().join("workspace")).unwrap();
        let base = temporary.path().join("export");
        let outside = temporary.path().join("outside");
        fs::create_dir(&base).unwrap();
        fs::create_dir(&outside).unwrap();
        let context = OutputContext::on_date(
            base.clone(),
            OutputOrganization::ByDate,
            NaiveDate::from_ymd_opt(2026, 1, 9).unwrap(),
        );
        let target = base.join("2026-01-09");
        let linked = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&target)
            .arg(&outside)
            .output()
            .unwrap();
        assert!(
            linked.status.success(),
            "{}",
            String::from_utf8_lossy(&linked.stderr)
        );
        let rejected = prepare(&context, &target, workspace.root(), true).is_err();
        fs::remove_dir(&target).unwrap();
        assert!(rejected);
        assert_eq!(fs::read_dir(outside).unwrap().count(), 0);
    }
}
