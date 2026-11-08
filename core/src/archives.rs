mod readers;
mod session;
pub(crate) use session::template_directory;
pub use session::{
    ArchiveClassification, ArchiveContent, ArchivePhase, ArchiveProgress, ArchiveRoot,
    ArchiveSession, ArchiveSessionView, ImportAction,
};

use crate::{
    error::AppError,
    filesystem::{case_key, check_chain, validate_segment},
};
use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    io::{self, Read, Seek, SeekFrom, Write},
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

pub const GIB: u64 = 1024 * 1024 * 1024;
pub const DECODER_LIMIT: u64 = 512 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct ArchiveEntry {
    pub path: String,
    pub directory: bool,
    pub size: u64,
}

#[derive(Debug, Clone)]
pub struct ArchiveLimits {
    pub entries: u64,
    pub expanded_bytes: u64,
    pub file_bytes: u64,
    pub timeout: Duration,
}

impl Default for ArchiveLimits {
    fn default() -> Self {
        Self {
            entries: 200_000,
            expanded_bytes: 100 * GIB,
            file_bytes: 50 * GIB,
            timeout: Duration::from_secs(4 * 3600),
        }
    }
}

struct ControlState {
    cancelled: AtomicBool,
    started: Instant,
    limits: ArchiveLimits,
    entries: AtomicU64,
    declared: AtomicU64,
    written: AtomicU64,
    processed: AtomicU64,
    phase: Mutex<ArchivePhase>,
    failure: Mutex<Option<AppError>>,
    rar: rars::ReadCancellation,
}

#[derive(Clone)]
pub struct ArchiveControl(Arc<ControlState>);

impl Default for ArchiveControl {
    fn default() -> Self {
        Self::new(ArchiveLimits::default())
    }
}

impl ArchiveControl {
    pub fn new(limits: ArchiveLimits) -> Self {
        Self(Arc::new(ControlState {
            cancelled: AtomicBool::new(false),
            started: Instant::now(),
            limits,
            entries: AtomicU64::new(0),
            declared: AtomicU64::new(0),
            written: AtomicU64::new(0),
            processed: AtomicU64::new(0),
            phase: Mutex::new(ArchivePhase::Starting),
            failure: Mutex::new(None),
            rar: rars::ReadCancellation::new(),
        }))
    }
    pub fn cancel(&self) {
        self.0.cancelled.store(true, Ordering::Relaxed);
        self.0.rar.cancel();
    }
    pub fn check(&self) -> Result<(), AppError> {
        if self.0.started.elapsed() >= self.0.limits.timeout {
            self.0.rar.cancel();
            return Err(AppError::new("archiveTimeout"));
        }
        if self.0.cancelled.load(Ordering::Relaxed) {
            return Err("archiveCancelled".into());
        }
        Ok(())
    }
    pub fn bytes(&self) -> u64 {
        self.0.written.load(Ordering::Relaxed)
    }
    pub fn entries(&self) -> u64 {
        self.0.entries.load(Ordering::Relaxed)
    }
    pub fn phase(&self) -> ArchivePhase {
        *self.0.phase.lock().unwrap()
    }
    pub fn set_phase(&self, phase: ArchivePhase) {
        *self.0.phase.lock().unwrap() = phase;
    }
    pub fn processed_bytes(&self) -> u64 {
        self.0.processed.load(Ordering::Relaxed)
    }
    pub fn add_processed(&self, bytes: u64) {
        self.0.processed.fetch_add(bytes, Ordering::Relaxed);
    }
    fn io_check(&self) -> io::Result<()> {
        self.check().map_err(io::Error::other)
    }
    fn remember(&self, error: AppError) -> io::Error {
        *self.0.failure.lock().unwrap() = Some(error.clone());
        io::Error::other(error)
    }
    fn decode_error(&self, error: impl std::fmt::Display) -> AppError {
        if let Err(error) = self.check() {
            return error;
        }
        self.0
            .failure
            .lock()
            .unwrap()
            .take()
            .unwrap_or_else(|| AppError::diagnostic("archiveInvalid", error))
    }
    fn admit(&self, entries: &[ArchiveEntry], packed: u64) -> Result<(), AppError> {
        self.check()?;
        let mut total = 0_u64;
        for entry in entries {
            if entry.size > self.0.limits.file_bytes {
                return Err("archiveLimit".into());
            }
            total = total.checked_add(entry.size).ok_or("archiveLimit")?;
        }
        if total > GIB && total / packed.max(1) > 1000 {
            return Err("archiveRatio".into());
        }
        let count = self
            .0
            .entries
            .fetch_add(entries.len() as u64, Ordering::Relaxed);
        let declared = self.0.declared.fetch_add(total, Ordering::Relaxed);
        if count.saturating_add(entries.len() as u64) > self.0.limits.entries
            || declared.saturating_add(total) > self.0.limits.expanded_bytes
        {
            return Err("archiveLimit".into());
        }
        Ok(())
    }
}

// Reads and seeks also check cancellation; decoder-internal work is cooperatively
// interrupted by the library where supported, never by terminating a thread.
struct ControlledReader {
    file: File,
    control: ArchiveControl,
}
impl Read for ControlledReader {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        self.control.io_check()?;
        self.file.read(buffer)
    }
}
impl Seek for ControlledReader {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        self.control.io_check()?;
        self.file.seek(position)
    }
}

struct EntryWriter {
    file: Option<File>,
    expected: u64,
    written: u64,
    control: ArchiveControl,
}
impl Write for EntryWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.control.io_check()?;
        if self.written.saturating_add(bytes.len() as u64) > self.expected
            || self.control.bytes().saturating_add(bytes.len() as u64)
                > self.control.0.limits.expanded_bytes
        {
            return Err(self.control.remember("archiveLimit".into()));
        }
        let count = match &mut self.file {
            Some(file) => file.write(bytes)?,
            None => bytes.len(),
        };
        self.written += count as u64;
        self.control.add_processed(count as u64);
        self.control
            .0
            .written
            .fetch_add(count as u64, Ordering::Relaxed);
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        if let Some(file) = &mut self.file {
            file.flush()?;
        }
        Ok(())
    }
}

fn checked_path(name: &str, directory: bool) -> Result<String, AppError> {
    let name = name.replace('\\', "/");
    let path = if directory {
        name.strip_suffix('/').unwrap_or(&name)
    } else {
        &name
    };
    if path.is_empty() {
        return Err("archiveUnsafePath".into());
    }
    for segment in path.split('/') {
        validate_segment(segment).map_err(|error| {
            AppError::new("archiveUnsafePath")
                .param("path", &name)
                .caused_by([error])
        })?;
        if matches!(segment, "." | "..") {
            return Err(AppError::new("archiveUnsafePath").param("path", &name));
        }
    }
    Ok(path.to_owned())
}

fn validate_inventory(entries: &[ArchiveEntry]) -> Result<(), AppError> {
    let mut paths = HashMap::new();
    for entry in entries {
        if entry.directory && entry.size != 0 {
            return Err("archiveInvalid".into());
        }
        if paths.insert(case_key(&entry.path), entry).is_some() {
            return Err(AppError::new("archiveDuplicate").param("path", &entry.path));
        }
    }
    let mut directories = HashMap::new();
    for entry in entries {
        let mut parent = entry.path.as_str();
        while let Some((path, _)) = parent.rsplit_once('/') {
            let key = case_key(path);
            if paths
                .get(&key)
                .is_some_and(|entry| !entry.directory || entry.path != path)
                || directories
                    .insert(key, path)
                    .is_some_and(|existing| existing != path)
            {
                return Err(AppError::new("archiveDuplicate").param("path", path));
            }
            parent = path;
        }
    }
    Ok(())
}

fn output(
    root: &Path,
    entry: &ArchiveEntry,
    control: &ArchiveControl,
) -> Result<EntryWriter, AppError> {
    control.check()?;
    check_chain(root)?;
    let segments: Vec<_> = entry.path.split('/').collect();
    let mut parent = root.to_path_buf();
    for segment in segments
        .iter()
        .take(segments.len() - usize::from(!entry.directory))
    {
        parent.push(segment);
        match fs::create_dir(&parent) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(AppError::diagnostic("ioError", error)),
        }
        check_chain(&parent)?;
        if !parent.is_dir() {
            return Err("archiveDuplicate".into());
        }
    }
    let file = if entry.directory {
        None
    } else {
        let target = parent.join(segments.last().unwrap());
        Some(
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&target)
                .map_err(|error| {
                    AppError::diagnostic("ioError", error).param("path", target.display())
                })?,
        )
    };
    Ok(EntryWriter {
        file,
        expected: entry.size,
        written: 0,
        control: control.clone(),
    })
}

pub fn extract_archive(
    source: &Path,
    destination: &Path,
    control: &ArchiveControl,
) -> Result<Vec<ArchiveEntry>, AppError> {
    control.check()?;
    let file = crate::filesystem::open_regular_file(source)?;
    let packed = file
        .metadata()
        .map_err(|error| AppError::diagnostic("ioError", error))?
        .len();
    let mut input = ControlledReader {
        file,
        control: control.clone(),
    };
    control.set_phase(ArchivePhase::Inventory);
    let mut signature = [0_u8; 8];
    let size = input
        .read(&mut signature)
        .map_err(|error| control.decode_error(error))?;
    input
        .seek(SeekFrom::Start(0))
        .map_err(|error| control.decode_error(error))?;
    *control.0.failure.lock().unwrap() = None;
    if signature.starts_with(b"PK") {
        readers::zip(input, destination, packed, control)
    } else if signature[..size].starts_with(b"Rar!\x1a\x07") {
        readers::rar(input, destination, packed, control)
    } else if signature[..size].starts_with(b"7z\xbc\xaf\x27\x1c") {
        readers::sevenz(input, destination, packed, control)
    } else {
        Err("archiveUnsupported".into())
    }
}
