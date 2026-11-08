use dim_creator_core::archives::{ArchiveControl, extract_archive};
use dim_creator_core::model::Settings;
use std::{fs, io::Write, path::Path};
use tempfile::tempdir;
use zip::{ZipWriter, write::SimpleFileOptions};

fn zip_file(path: &Path, entries: &[(&str, &[u8])]) {
    let mut writer = ZipWriter::new(fs::File::create(path).unwrap());
    for (name, bytes) in entries {
        writer
            .start_file(*name, SimpleFileOptions::default())
            .unwrap();
        writer.write_all(bytes).unwrap();
    }
    writer.finish().unwrap();
}

#[test]
fn zip_inventory_is_validated_before_any_output_is_written() {
    for unsafe_name in [
        "../escape",
        "C:/escape",
        "People/CON .txt",
        "People/a:stream",
        "/absolute",
    ] {
        let dir = tempdir().unwrap();
        let source = dir.path().join("input.zip");
        let destination = dir.path().join("out");
        fs::create_dir(&destination).unwrap();
        zip_file(
            &source,
            &[("People/good.duf", b"keep"), (unsafe_name, b"bad")],
        );
        assert!(
            extract_archive(&source, &destination, &ArchiveControl::default()).is_err(),
            "{unsafe_name}"
        );
        assert_eq!(fs::read_dir(destination).unwrap().count(), 0);
    }
}

#[test]
fn zip_rejects_windows_collisions_and_file_directory_conflicts() {
    for names in [["People/A.duf", "people/a.duf"], ["People", "People/a.duf"]] {
        let dir = tempdir().unwrap();
        let source = dir.path().join("input.zip");
        let destination = dir.path().join("out");
        fs::create_dir(&destination).unwrap();
        zip_file(&source, &[(names[0], b"one"), (names[1], b"two")]);
        assert!(extract_archive(&source, &destination, &ArchiveControl::default()).is_err());
        assert_eq!(fs::read_dir(destination).unwrap().count(), 0);
    }
}

#[test]
fn extraction_preserves_bytes_and_original_archive_and_obeys_cancellation() {
    let dir = tempdir().unwrap();
    let source = dir.path().join("input.zip");
    let destination = dir.path().join("out");
    fs::create_dir(&destination).unwrap();
    zip_file(
        &source,
        &[
            ("My Library/People/item.duf", b"content"),
            ("My Library/readme.txt", b"readme"),
        ],
    );
    let original = fs::read(&source).unwrap();
    let control = ArchiveControl::default();
    let inventory = extract_archive(&source, &destination, &control).unwrap();
    assert_eq!(inventory.len(), 2);
    assert_eq!(
        fs::read(destination.join("My Library/People/item.duf")).unwrap(),
        b"content"
    );
    assert_eq!(fs::read(&source).unwrap(), original);
    control.cancel();
    assert_eq!(
        extract_archive(&source, &destination, &control)
            .unwrap_err()
            .code,
        "archiveCancelled"
    );
}

#[test]
fn session_finds_wrapped_and_nested_content_and_reuses_verified_data() {
    use dim_creator_core::archives::ArchiveSession;
    let dir = tempdir().unwrap();
    let nested = dir.path().join("Part 2.zip");
    zip_file(&nested, &[("Library/Runtime/item.dsf", b"nested")]);
    let outer = dir.path().join("Collection.zip");
    zip_file(
        &outer,
        &[
            ("Part 2.zip", &fs::read(&nested).unwrap()),
            ("Wrapper/Content/People/item.duf", b"direct"),
            ("Wrapper/Content/readme.txt", b"extra"),
        ],
    );
    let control = ArchiveControl::default();
    let session = ArchiveSession::analyze(
        &[outer],
        dir.path(),
        &Settings {
            daz_folders: vec!["People".into(), "Runtime".into()],
            ..Settings::default()
        },
        &control,
        |_| {},
    )
    .unwrap();
    let view = session.view();
    assert_eq!(view.contents.len(), 2);
    assert!(view.contents.iter().all(|content| content.issue.is_none()));
    let direct = view
        .contents
        .iter()
        .find(|content| content.archive_name == "Collection.zip")
        .unwrap();
    assert_eq!(direct.roots[0].path, "Wrapper/Content");
    assert_eq!(direct.roots[0].files, 2);
    let nested = view
        .contents
        .iter()
        .find(|content| content.archive_name == "Part 2.zip")
        .unwrap();
    assert_eq!(nested.origin, ["Collection.zip"]);
    assert_eq!(direct.suggested_group, nested.suggested_group);
    let before = control.bytes();
    assert_eq!(
        session
            .files(&direct.id, &direct.roots[0].id)
            .unwrap()
            .len(),
        2
    );
    assert_eq!(
        session
            .files(&nested.id, &nested.roots[0].id)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(control.bytes(), before);
}

#[test]
fn detection_prioritizes_content_and_keeps_plain_archives_selectable() {
    use dim_creator_core::archives::{ArchiveClassification, ArchiveSession};
    let dir = tempdir().unwrap();
    let template = dir.path().join("Product Templates.zip");
    zip_file(&template, &[("People/template.txt", b"template")]);
    let ordinary = dir.path().join("Readme.zip");
    zip_file(&ordinary, &[("readme.txt", b"readme")]);
    let view = ArchiveSession::analyze(
        &[template, ordinary],
        dir.path(),
        &Settings {
            daz_folders: vec!["People".into()],
            ..Settings::default()
        },
        &ArchiveControl::default(),
        |_| {},
    )
    .unwrap()
    .view();
    assert_eq!(view.contents.len(), 2);
    assert_eq!(
        view.contents[0].classification,
        ArchiveClassification::Content
    );
    assert_eq!(
        view.contents[1].classification,
        ArchiveClassification::NoContent
    );

    let wire = serde_json::to_value(view).unwrap();
    assert_eq!(wire["contents"][0]["classification"], "content");
    assert_eq!(wire["contents"][1]["classification"], "noContent");
    assert_eq!(
        wire["contents"][0].get("issue"),
        Some(&serde_json::Value::Null)
    );
    assert_eq!(
        wire["contents"][1].get("issue"),
        Some(&serde_json::Value::Null)
    );
}

#[test]
fn actual_solid_rar4_and_rar5_fixtures_extract_completely() {
    // Apache-2.0 fixtures from rars 0.12.3, tests/fixtures: rar15_40/rars_generated/solid.rar and rar50/solid.rar.
    for name in ["rar4-solid.rar", "rar5-solid.rar"] {
        let dir = tempdir().unwrap();
        let source = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/archives")
            .join(name);
        let entries = extract_archive(&source, dir.path(), &ArchiveControl::default()).unwrap();
        assert!(entries.len() >= 2);
        for entry in entries.iter().filter(|entry| !entry.directory) {
            let bytes = fs::read(dir.path().join(&entry.path)).unwrap();
            assert_eq!(bytes.len() as u64, entry.size);
        }
        if name == "rar4-solid.rar" {
            assert_eq!(
                fs::read(dir.path().join(&entries[0].path)).unwrap(),
                b"rar15 oracle payload\n"
            );
            assert_eq!(
                fs::read(dir.path().join(&entries[1].path)).unwrap(),
                b"rar15 oracle second file\n"
            );
        }
    }
}

#[test]
fn bzip2_solid_7z_extracts_nested_content_and_rejects_truncation() {
    use sevenz_rust2::{
        ArchiveEntry, ArchiveWriter, EncoderConfiguration, EncoderMethod, SourceReader,
    };
    let dir = tempdir().unwrap();
    let nested = dir.path().join("nested.zip");
    zip_file(&nested, &[("Content/People/item.duf", b"nested bytes")]);
    let source = dir.path().join("solid.7z");
    let mut writer = ArchiveWriter::new(fs::File::create(&source).unwrap()).unwrap();
    writer.set_content_methods(vec![EncoderConfiguration::new(EncoderMethod::BZIP2)]);
    writer
        .push_archive_entries(
            vec![
                ArchiveEntry::new_file("Content/Runtime/test.dsf"),
                ArchiveEntry::new_file("nested.zip"),
            ],
            vec![
                SourceReader::new(std::io::Cursor::new(b"direct bytes".to_vec())),
                SourceReader::new(std::io::Cursor::new(fs::read(nested).unwrap())),
            ],
        )
        .unwrap();
    writer.finish().unwrap();
    let control = ArchiveControl::default();
    let session = dim_creator_core::archives::ArchiveSession::analyze(
        std::slice::from_ref(&source),
        dir.path(),
        &Settings {
            daz_folders: vec!["Runtime".into(), "People".into()],
            ..Settings::default()
        },
        &control,
        |_| {},
    )
    .unwrap();
    let view = session.view();
    assert_eq!(view.contents.len(), 2, "{view:?}");
    assert!(
        view.contents.iter().all(|item| item.issue.is_none()),
        "{view:?}"
    );
    drop(session);
    let file = fs::OpenOptions::new().write(true).open(&source).unwrap();
    file.set_len(40).unwrap();
    drop(file);
    let out = dir.path().join("broken");
    fs::create_dir(&out).unwrap();
    assert!(extract_archive(&source, &out, &ArchiveControl::default()).is_err());
    assert_eq!(fs::read_dir(out).unwrap().count(), 0);
}

#[test]
fn header_disagreement_and_duplicate_zip_names_fail_before_writes() {
    for mode in ["header", "duplicate"] {
        let dir = tempdir().unwrap();
        let source = dir.path().join("input.zip");
        let out = dir.path().join("out");
        fs::create_dir(&out).unwrap();
        zip_file(
            &source,
            &[("People/one.duf", b"one"), ("People/two.duf", b"two")],
        );
        let mut bytes = fs::read(&source).unwrap();
        let positions: Vec<_> = bytes
            .windows(14)
            .enumerate()
            .filter(|(_, v)| *v == b"People/two.duf")
            .map(|(i, _)| i)
            .collect();
        // Mutate only the local header, or both equal-length names to make a duplicate.
        let count = if mode == "header" { 1 } else { positions.len() };
        for index in positions.into_iter().take(count) {
            bytes[index..index + 14].copy_from_slice(b"People/one.duf");
        }
        fs::write(&source, bytes).unwrap();
        assert!(extract_archive(&source, &out, &ArchiveControl::default()).is_err());
        assert_eq!(fs::read_dir(out).unwrap().count(), 0);
    }
}

#[test]
fn declared_limits_and_deadline_reject_before_file_creation() {
    use dim_creator_core::archives::ArchiveLimits;
    for limits in [
        ArchiveLimits {
            entries: 1,
            ..Default::default()
        },
        ArchiveLimits {
            file_bytes: 2,
            ..Default::default()
        },
        ArchiveLimits {
            expanded_bytes: 3,
            ..Default::default()
        },
        ArchiveLimits {
            timeout: std::time::Duration::ZERO,
            ..Default::default()
        },
    ] {
        let dir = tempdir().unwrap();
        let source = dir.path().join("input.zip");
        let out = dir.path().join("out");
        fs::create_dir(&out).unwrap();
        zip_file(
            &source,
            &[("People/a.duf", b"123"), ("People/b.duf", b"456")],
        );
        assert!(extract_archive(&source, &out, &ArchiveControl::new(limits)).is_err());
        assert_eq!(fs::read_dir(out).unwrap().count(), 0);
    }
}

#[test]
fn zip64_and_crc_integrity_are_checked_without_changing_payloads() {
    let dir = tempdir().unwrap();
    let source = dir.path().join("input.zip");
    let out = dir.path().join("out");
    fs::create_dir(&out).unwrap();
    let mut zip = ZipWriter::new(fs::File::create(&source).unwrap());
    zip.start_file(
        "People/test.duf",
        SimpleFileOptions::default()
            .large_file(true)
            .compression_method(zip::CompressionMethod::Stored),
    )
    .unwrap();
    zip.write_all(b"known payload").unwrap();
    zip.finish().unwrap();
    extract_archive(&source, &out, &ArchiveControl::default()).unwrap();
    assert_eq!(
        fs::read(out.join("People/test.duf")).unwrap(),
        b"known payload"
    );
    let mut bytes = fs::read(&source).unwrap();
    let index = bytes
        .windows(13)
        .position(|b| b == b"known payload")
        .unwrap();
    bytes[index] ^= 1;
    fs::write(&source, bytes).unwrap();
    let bad = dir.path().join("bad");
    fs::create_dir(&bad).unwrap();
    assert!(extract_archive(&source, &bad, &ArchiveControl::default()).is_err());
}

#[test]
fn zip_windows_reparse_attributes_are_rejected_before_writes() {
    let dir = tempdir().unwrap();
    let source = dir.path().join("input.zip");
    let out = dir.path().join("out");
    fs::create_dir(&out).unwrap();
    zip_file(&source, &[("People/link.duf", b"opaque link target")]);
    let mut bytes = fs::read(&source).unwrap();
    let central = bytes.windows(4).position(|b| b == b"PK\x01\x02").unwrap();
    bytes[central + 5] = 10; // ZIP originating system: Windows NTFS.
    bytes[central + 38..central + 42].copy_from_slice(&0x400u32.to_le_bytes());
    fs::write(&source, bytes).unwrap();
    assert_eq!(
        extract_archive(&source, &out, &ArchiveControl::default())
            .unwrap_err()
            .code,
        "archiveUnsupportedType"
    );
    assert_eq!(fs::read_dir(out).unwrap().count(), 0);
}

#[test]
fn many_disjoint_roots_share_inventory_and_keep_each_units_own_files() {
    let dir = tempdir().unwrap();
    let source = dir.path().join("many.zip");
    let mut zip = ZipWriter::new(fs::File::create(&source).unwrap());
    for index in 0..10000 {
        zip.start_file(
            format!("Product{index}/People/a.duf"),
            SimpleFileOptions::default(),
        )
        .unwrap();
        zip.write_all(b"x").unwrap();
    }
    zip.finish().unwrap();
    let control = ArchiveControl::default();
    let session = dim_creator_core::archives::ArchiveSession::analyze(
        &[source],
        dir.path(),
        &Settings {
            daz_folders: vec!["People".into()],
            ..Settings::default()
        },
        &control,
        |_| {},
    )
    .unwrap();
    let view = session.view();
    assert_eq!(view.contents.len(), 10000);
    for unit in &view.contents {
        let files = session.files(&unit.id, &unit.roots[0].id).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].0, "People/a.duf");
    }
    assert_eq!(control.bytes(), 10000);
}
