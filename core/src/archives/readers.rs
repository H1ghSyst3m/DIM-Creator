use super::*;

fn reject_type(directory: bool, unix: Option<u32>, windows: Option<u32>) -> Result<(), AppError> {
    if windows.is_some_and(|flags| flags & 0x400 != 0)
        || unix.is_some_and(|mode| {
            let kind = mode & 0o170000;
            kind != 0 && kind != if directory { 0o040000 } else { 0o100000 }
        })
    {
        return Err("archiveUnsupportedType".into());
    }
    Ok(())
}

fn prepare(
    entries: &[ArchiveEntry],
    packed: u64,
    destination: &Path,
    control: &ArchiveControl,
) -> Result<(), AppError> {
    control.set_phase(ArchivePhase::Security);
    validate_inventory(entries)?;
    control.admit(entries, packed)?;
    check_chain(destination)?;
    let expanded = entries.iter().map(|entry| entry.size).sum::<u64>();
    crate::filesystem::require_space(
        destination,
        expanded.saturating_mul(3).saturating_add(2 * GIB),
    )?;
    control.set_phase(ArchivePhase::Extract);
    Ok(())
}

pub(super) fn zip(
    input: ControlledReader,
    destination: &Path,
    packed: u64,
    control: &ArchiveControl,
) -> Result<Vec<ArchiveEntry>, AppError> {
    let mut archive = ::zip::ZipArchive::new(input).map_err(|error| control.decode_error(error))?;
    if archive.has_duplicate_names() {
        return Err("archiveDuplicate".into());
    }
    archive
        .validate_header_consistency()
        .map_err(|error| control.decode_error(error))?;
    let mut entries = Vec::new();
    for index in 0..archive.len() {
        control.check()?;
        let file = archive
            .by_index_raw(index)
            .map_err(|error| control.decode_error(error))?;
        if file.encrypted() {
            return Err("archiveEncrypted".into());
        }
        if !matches!(
            file.compression(),
            ::zip::CompressionMethod::Stored | ::zip::CompressionMethod::Deflated
        ) {
            return Err("archiveUnsupported".into());
        }
        reject_type(
            file.is_dir(),
            file.unix_mode(),
            Some(file.external_attributes() & 0xffff),
        )?;
        let path = checked_path(
            &file.name().map_err(|error| control.decode_error(error))?,
            file.is_dir(),
        )?;
        entries.push(ArchiveEntry {
            path,
            directory: file.is_dir(),
            size: file.size(),
        });
    }
    prepare(&entries, packed, destination, control)?;
    for (index, entry) in entries.iter().enumerate() {
        let mut source = archive
            .by_index(index)
            .map_err(|error| control.decode_error(error))?;
        let mut target = output(destination, entry, control)?;
        io::copy(&mut source, &mut target).map_err(|error| control.decode_error(error))?;
        if target.written != entry.size {
            return Err("archiveInvalid".into());
        }
        target
            .flush()
            .map_err(|error| control.decode_error(error))?;
    }
    Ok(entries)
}

fn rar_options<'a>(
    control: &'a ArchiveControl,
    scratch: &'a rars::Rar50Scratch,
) -> rars::ArchiveReadOptions<'a> {
    let mut options = rars::ArchiveReadOptions::default();
    options.cancellation = Some(&control.0.rar);
    options.max_header_count = Some(control.0.limits.entries.saturating_add(1024));
    options.max_header_bytes = Some(64 * 1024 * 1024);
    options.rar50_buffered_decode_limit = Some(1024 * 1024);
    options.rar50_scratch = Some(scratch);
    options.rar50_dictionary_size_limit = Some(DECODER_LIMIT);
    options.max_reader_workspace_bytes = Some(DECODER_LIMIT);
    options.max_member_output_bytes = Some(control.0.limits.file_bytes);
    options.max_total_output_bytes = Some(
        control
            .0
            .limits
            .expanded_bytes
            .saturating_sub(control.bytes()),
    );
    options
}

fn rar_error(control: &ArchiveControl, error: rars::Error) -> AppError {
    let code = match error.kind() {
        rars::ErrorKind::PasswordRequired | rars::ErrorKind::BadPassword => "archiveEncrypted",
        rars::ErrorKind::UnsupportedFormat | rars::ErrorKind::UnsupportedFeature => {
            "archiveUnsupported"
        }
        rars::ErrorKind::ResourceLimit => "archiveLimit",
        _ => return control.decode_error(error),
    };
    AppError::diagnostic(code, error)
}

pub(super) fn rar(
    input: ControlledReader,
    destination: &Path,
    packed: u64,
    control: &ArchiveControl,
) -> Result<Vec<ArchiveEntry>, AppError> {
    let scratch_dir = tempfile::tempdir_in(destination.parent().ok_or("archiveInvalid")?)
        .map_err(|error| control.decode_error(error))?;
    let scratch = rars::Rar50Scratch::new(scratch_dir.path(), control.0.limits.expanded_bytes)
        .with_filter_memory_limit(DECODER_LIMIT);
    let archive =
        rars::ArchiveReader::read_reader_with_options(input, rar_options(control, &scratch))
            .map_err(|error| rar_error(control, error))?;
    if matches!(archive, rars::Archive::Rar13(_)) {
        return Err("archiveUnsupported".into());
    }
    if match &archive {
        rars::Archive::Rar15To40(archive) => archive.main.is_volume(),
        rars::Archive::Rar50Plus(archive) => archive.main.is_volume(),
        _ => false,
    } {
        return Err("archiveVolumes".into());
    }
    let mut entries = Vec::new();
    for member in archive.members() {
        control.check()?;
        let meta = &member.meta;
        if meta.is_redirection {
            return Err("archiveUnsupportedType".into());
        }
        if meta.is_split_before || meta.is_split_after {
            return Err("archiveVolumes".into());
        }
        if meta.is_encrypted {
            return Err("archiveEncrypted".into());
        }
        let unix = match meta.attr_source() {
            rars::AttrSource::Unix => Some(meta.file_attr as u32),
            rars::AttrSource::Dos => None,
            _ => return Err("archiveUnsupportedType".into()),
        };
        reject_type(
            meta.is_directory,
            unix,
            unix.is_none().then_some(meta.file_attr as u32),
        )?;
        let name = member
            .decoded_name(None)
            .map_err(|error| rar_error(control, error))?;
        let name = std::str::from_utf8(&name).map_err(|_| AppError::new("archiveUnsafePath"))?;
        entries.push(ArchiveEntry {
            path: checked_path(name, meta.is_directory)?,
            directory: meta.is_directory,
            size: meta.unpacked_size,
        });
    }
    prepare(&entries, packed, destination, control)?;
    let mut index = 0;
    archive
        .extract_to_with_options(rar_options(control, &scratch), |meta| {
            let entry = entries
                .get(index)
                .ok_or_else(|| rars::Error::from(io::Error::other("unexpected archive entry")))?;
            index += 1;
            let name = rars::filename::decoded_name(&meta.name, meta.name_is_unicode, None)?;
            let name = std::str::from_utf8(&name)
                .map_err(|error| rars::Error::from(io::Error::other(error.to_string())))?;
            if checked_path(name, meta.is_directory)
                .map_err(|error| rars::Error::from(control.remember(error)))?
                != entry.path
                || meta.is_directory != entry.directory
            {
                return Err(rars::Error::from(control.remember("archiveInvalid".into())));
            }
            let writer = output(destination, entry, control)
                .map_err(|error| rars::Error::from(control.remember(error)))?;
            Ok(Box::new(writer))
        })
        .map_err(|error| rar_error(control, error))?;
    if index != entries.len() {
        return Err("archiveInvalid".into());
    }
    for entry in &entries {
        if !entry.directory
            && fs::metadata(destination.join(&entry.path))
                .map_err(|error| control.decode_error(error))?
                .len()
                != entry.size
        {
            return Err("archiveInvalid".into());
        }
    }
    Ok(entries)
}

fn sevenz_error(control: &ArchiveControl, error: sevenz_rust2::Error) -> AppError {
    let code = match &error {
        sevenz_rust2::Error::PasswordRequired | sevenz_rust2::Error::MaybeBadPassword(_) => {
            "archiveEncrypted"
        }
        sevenz_rust2::Error::Unsupported(_)
        | sevenz_rust2::Error::UnsupportedCompressionMethod(_) => "archiveUnsupported",
        sevenz_rust2::Error::MaxMemLimited { .. } => "archiveLimit",
        _ => return control.decode_error(error),
    };
    AppError::diagnostic(code, error)
}

fn sevenz_memory(archive: &sevenz_rust2::Archive) -> Result<(), AppError> {
    for block in &archive.blocks {
        let mut memory = 0_u64;
        for coder in &block.coders {
            let properties = coder.properties();
            let needed = match coder.encoder_method_id() {
                [0x21] => {
                    let value = *properties.first().ok_or("archiveInvalid")?;
                    if value > 40 {
                        return Err("archiveInvalid".into());
                    }
                    if value == 40 {
                        u32::MAX as u64
                    } else {
                        (2 | u64::from(value & 1)) << (u32::from(value) / 2 + 11)
                    }
                }
                [3, 1, 1] | [3, 4, 1] => u64::from(u32::from_le_bytes(
                    properties
                        .get(1..5)
                        .ok_or("archiveInvalid")?
                        .try_into()
                        .unwrap(),
                )),
                [6, 0xf1, 7, 1] => return Err("archiveEncrypted".into()),
                sevenz_rust2::EncoderMethod::ID_COPY
                | sevenz_rust2::EncoderMethod::ID_DELTA
                | sevenz_rust2::EncoderMethod::ID_BZIP2
                | sevenz_rust2::EncoderMethod::ID_DEFLATE
                | sevenz_rust2::EncoderMethod::ID_BCJ_X86
                | sevenz_rust2::EncoderMethod::ID_BCJ2
                | sevenz_rust2::EncoderMethod::ID_BCJ_PPC
                | sevenz_rust2::EncoderMethod::ID_BCJ_IA64
                | sevenz_rust2::EncoderMethod::ID_BCJ_ARM
                | sevenz_rust2::EncoderMethod::ID_BCJ_ARM64
                | sevenz_rust2::EncoderMethod::ID_BCJ_ARM_THUMB
                | sevenz_rust2::EncoderMethod::ID_BCJ_SPARC
                | sevenz_rust2::EncoderMethod::ID_BCJ_RISCV => 0,
                _ => return Err("archiveUnsupported".into()),
            };
            memory = memory.saturating_add(needed);
        }
        if memory > DECODER_LIMIT {
            return Err("archiveLimit".into());
        }
    }
    Ok(())
}

pub(super) fn sevenz(
    input: ControlledReader,
    destination: &Path,
    packed: u64,
    control: &ArchiveControl,
) -> Result<Vec<ArchiveEntry>, AppError> {
    let mut archive = sevenz_rust2::ArchiveReader::new(input, sevenz_rust2::Password::empty())
        .map_err(|error| sevenz_error(control, error))?;
    archive.set_thread_count(1);
    sevenz_memory(archive.archive())?;
    let mut entries = Vec::new();
    for file in &archive.archive().files {
        if file.is_anti_item {
            return Err("archiveUnsupportedType".into());
        }
        reject_type(
            file.is_directory,
            file.has_windows_attributes
                .then_some(file.windows_attributes >> 16),
            file.has_windows_attributes
                .then_some(file.windows_attributes),
        )?;
        entries.push(ArchiveEntry {
            path: checked_path(&file.name, file.is_directory)?,
            directory: file.is_directory,
            size: file.size,
        });
    }
    prepare(&entries, packed, destination, control)?;
    let mut visited = std::collections::HashSet::new();
    archive
        .for_each_entries(|file, source| {
            let path = checked_path(&file.name, file.is_directory)
                .map_err(|error| sevenz_rust2::Error::from(control.remember(error)))?;
            if !visited.insert(path.clone()) {
                return Err(io::Error::other("duplicate extraction entry").into());
            }
            let entry = ArchiveEntry {
                path,
                directory: file.is_directory,
                size: file.size,
            };
            let mut target = output(destination, &entry, control)
                .map_err(|error| sevenz_rust2::Error::from(control.remember(error)))?;
            io::copy(source, &mut target)?;
            if target.written != entry.size {
                return Err(control.remember("archiveInvalid".into()).into());
            }
            target.flush()?;
            Ok(true)
        })
        .map_err(|error| sevenz_error(control, error))?;
    if visited.len() != entries.len() {
        return Err("archiveInvalid".into());
    }
    Ok(entries)
}
