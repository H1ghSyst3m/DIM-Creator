use crate::error::AppError;
use std::{
    fs::{self, File, Metadata, OpenOptions},
    path::{Component, Path, PathBuf},
};

pub fn is_link(metadata: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

pub fn check_chain(path: &Path) -> Result<(), AppError> {
    if !path.is_absolute() || path.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(AppError::new("invalidAbsolutePath").param("path", path.display()));
    }
    #[cfg(windows)]
    if let Some(Component::Prefix(prefix)) = path.components().next() {
        use std::path::Prefix;
        if matches!(prefix.kind(), Prefix::DeviceNS(_) | Prefix::Verbatim(_)) {
            return Err(AppError::new("unsupportedDevicePath").param("path", path.display()));
        }
    }

    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        if matches!(component, Component::Prefix(_)) {
            continue;
        }
        let metadata = fs::symlink_metadata(&current).map_err(|error| {
            AppError::diagnostic("ioError", error).param("path", current.display())
        })?;
        if is_link(&metadata) {
            return Err(AppError::new("unsupportedLink").param("path", current.display()));
        }
    }
    Ok(())
}

pub fn open_regular_file(path: &Path) -> Result<File, AppError> {
    open_read(path, false)
}

pub(crate) fn open_directory(path: &Path) -> Result<File, AppError> {
    check_chain(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
            FILE_SHARE_WRITE,
        };
        options
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE);
    }
    let file = options
        .open(path)
        .map_err(|error| AppError::diagnostic("ioError", error).param("path", path.display()))?;
    let metadata = file
        .metadata()
        .map_err(|error| AppError::diagnostic("ioError", error))?;
    if !metadata.is_dir() || is_link(&metadata) {
        return Err(AppError::new("templateDirectoryInvalid").param("path", path.display()));
    }
    Ok(file)
}

pub(crate) fn open_replaceable_file(path: &Path) -> Result<File, AppError> {
    open_read(path, true)
}

#[cfg(windows)]
pub(crate) fn hash_file_identity(
    file: &File,
    hash: &mut impl std::hash::Hasher,
) -> std::io::Result<()> {
    use std::{hash::Hash, mem::MaybeUninit, os::windows::io::AsRawHandle};
    use windows_sys::Win32::Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, GetFileInformationByHandle,
    };

    let mut info = MaybeUninit::<BY_HANDLE_FILE_INFORMATION>::uninit();
    // A successful call initializes the structure for this live file handle.
    let info = unsafe {
        if GetFileInformationByHandle(file.as_raw_handle(), info.as_mut_ptr()) == 0 {
            return Err(std::io::Error::last_os_error());
        }
        info.assume_init()
    };
    (
        info.dwVolumeSerialNumber,
        info.nFileIndexHigh,
        info.nFileIndexLow,
    )
        .hash(hash);
    Ok(())
}

#[cfg(not(windows))]
pub(crate) fn hash_file_identity(
    _file: &File,
    _hash: &mut impl std::hash::Hasher,
) -> std::io::Result<()> {
    Ok(())
}

fn open_read(path: &Path, allow_replace: bool) -> Result<File, AppError> {
    check_chain(path)?;
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    if is_link(&metadata) || !metadata.is_file() {
        return Err(AppError::new("notRegularFile").param("path", path.display()));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_SHARE_READ: u32 = 1;
        const FILE_SHARE_DELETE: u32 = 4;
        let sharing = if allow_replace {
            FILE_SHARE_READ | FILE_SHARE_DELETE
        } else {
            FILE_SHARE_READ
        };
        options
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .share_mode(sharing);
    }
    #[cfg(not(windows))]
    let _ = allow_replace;
    let file = options
        .open(path)
        .map_err(|error| AppError::diagnostic("ioError", error).param("path", path.display()))?;
    let metadata = file
        .metadata()
        .map_err(|error| AppError::diagnostic("operationError", error))?;
    if !metadata.is_file() || is_link(&metadata) {
        return Err(AppError::new("notRegularFile").param("path", path.display()));
    }
    Ok(file)
}

pub fn validate_segment(name: &str) -> Result<(), AppError> {
    if name.is_empty()
        || name.trim() != name
        || name.ends_with('.')
        || name.encode_utf16().count() > 255
        || name.chars().any(|c| c < ' ' || "<>:\"/\\|?*".contains(c))
    {
        return Err(AppError::new("invalidNativeName").param("name", name));
    }

    let stem = name
        .split('.')
        .next()
        .unwrap_or_default()
        .trim_end()
        .to_ascii_uppercase();
    let device = matches!(
        stem.as_str(),
        "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
    ) || ["COM", "LPT"].iter().any(|prefix| {
        stem.strip_prefix(prefix).is_some_and(|suffix| {
            matches!(
                suffix,
                "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        })
    });
    if device {
        return Err(AppError::new("reservedNativeName").param("name", name));
    }
    Ok(())
}

pub(crate) fn case_key(name: &str) -> String {
    name.to_uppercase()
}

pub(crate) fn entry_name(path: &Path) -> Result<&str, AppError> {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| AppError::new("invalidFilename").param("path", path.display()))?;
    validate_segment(name)?;
    Ok(name)
}
pub fn path_contains(parent: &Path, child: &Path) -> bool {
    let mut child_parts = child.iter();
    parent.iter().all(|part| {
        child_parts.next().is_some_and(|value| {
            case_key(&part.to_string_lossy()) == case_key(&value.to_string_lossy())
        })
    })
}

/// Comparison key for native paths already checked at the filesystem boundary.
pub fn path_key(path: &Path) -> String {
    path.components()
        .map(|part| part.as_os_str().to_string_lossy().to_uppercase())
        .collect::<Vec<_>>()
        .join("/")
}

/// Available capacity for the current user on the actual destination volume.
pub fn require_space(path: &Path, bytes: u64) -> Result<(), AppError> {
    check_chain(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
        let path_wide: Vec<_> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut available = 0;
        // The null-terminated path and output live for the duration of the call.
        if unsafe {
            GetDiskFreeSpaceExW(
                path_wide.as_ptr(),
                &mut available,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(
                AppError::diagnostic("ioError", std::io::Error::last_os_error())
                    .param("path", path.display()),
            );
        }
        if available < bytes {
            return Err(AppError::new("archiveDiskSpace")
                .param("path", path.display())
                .param("bytes", bytes));
        }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = bytes;
        Err("unsupportedPlatform".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(windows)]
    #[test]
    fn streamed_read_refuses_concurrent_writes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("content.txt");
        fs::write(&path, "content").unwrap();
        let writer = OpenOptions::new().write(true).open(&path).unwrap();
        assert!(open_regular_file(&path).is_err());
        drop(writer);
        let reader = open_regular_file(&path).unwrap();
        assert!(OpenOptions::new().write(true).open(&path).is_err());
        drop(reader);
        assert!(OpenOptions::new().write(true).open(&path).is_ok());
    }

    #[test]
    fn windows_names_reject_path_escape_devices_and_streams() {
        for invalid in [
            "", ".", "..", "C:stream", "a/b", "a\\b", "COM1.foo", "LPT¹", "a.", "a ", "CON .txt",
        ] {
            assert!(validate_segment(invalid).is_err(), "{invalid}");
        }
        assert!(validate_segment("Gemälde 01.jpg").is_ok());
        assert!(validate_segment("COM10.txt").is_ok());
        assert!(validate_segment(&"😀".repeat(127)).is_ok());
        assert!(validate_segment(&"😀".repeat(128)).is_err());
        assert!(validate_segment(&"a".repeat(255)).is_ok());
        assert!(validate_segment(&"a".repeat(256)).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn path_chain_and_file_open_reject_symlink_ancestors() {
        let dir = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        std::fs::write(outside.path().join("data"), "test").unwrap();
        std::os::unix::fs::symlink(outside.path(), dir.path().join("outside")).unwrap();
        let file = dir.path().join("outside/data");
        assert!(check_chain(&file).is_err());
        assert!(open_regular_file(&file).is_err());
    }
}
