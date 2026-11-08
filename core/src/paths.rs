use crate::error::AppError;

/// Package paths use forward slashes and cannot address an absolute path or parent.
pub(crate) fn relative_segments(relative: &str) -> Result<Vec<&str>, AppError> {
    if relative.is_empty() {
        return Ok(Vec::new());
    }
    relative
        .split('/')
        .map(|segment| {
            if segment.is_empty() || matches!(segment, "." | "..") || segment.contains(['\\', ':'])
            {
                return Err(AppError::new("invalidPackagePath").param("path", relative));
            }
            Ok(segment)
        })
        .collect()
}
