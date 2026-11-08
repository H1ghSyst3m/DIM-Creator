use crate::error::{AppError, Severity};
use crate::paths::relative_segments;
use std::collections::HashSet;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::filesystem::{case_key, validate_segment};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct Package {
    pub id: String,
    pub name: String,
    pub store: String,
    pub prefix: String,
    pub auto_prefix: bool,
    pub sku: String,
    pub part: String,
    pub guid: String,
    pub tags: Vec<String>,
    pub cover: Option<String>,
    pub clean_support: bool,
    pub excluded: Vec<String>,
}

impl Package {
    pub(crate) fn new(name: &str, settings: &Settings) -> Result<Self, AppError> {
        let settings = settings.normalized();
        let store = settings
            .stores
            .iter()
            .find(|store| store.name == settings.default_store)
            .ok_or("defaultStoreInvalid")?;
        Ok(Self {
            id: Uuid::new_v4().to_string(),
            name: name.trim().to_owned(),
            store: store.name.clone(),
            prefix: store.prefix.clone(),
            auto_prefix: true,
            sku: String::new(),
            part: "01".into(),
            guid: Uuid::new_v4().to_string(),
            tags: settings.default_tags,
            cover: None,
            clean_support: true,
            excluded: Vec::new(),
        })
    }

    pub(crate) fn validate_storage(&self, expected_id: &str) -> Result<(), AppError> {
        if self.id != expected_id {
            return Err("packageIdMismatch".into());
        }
        if let Some(cover) = &self.cover {
            let id = cover
                .strip_prefix("cover-")
                .and_then(|name| name.strip_suffix(".jpg"))
                .ok_or("invalidCover")?;
            check_id(id)?;
        }
        for relative in &self.excluded {
            let segments = relative_segments(relative)?;
            for segment in &segments {
                validate_segment(segment)?;
            }
            if segments.is_empty() {
                return Err("contentRootProtected".into());
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, ts_rs::TS)]
pub struct Store {
    pub name: String,
    pub prefix: String,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub enum OutputOrganization {
    #[default]
    Flat,
    ByDate,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub language: String,
    pub theme: String,
    pub check_updates: bool,
    pub stores: Vec<Store>,
    pub tags: Vec<String>,
    pub default_store: String,
    pub default_tags: Vec<String>,
    pub output_dir: Option<String>,
    #[serde(default)]
    pub template_dir: Option<String>,
    #[serde(default)]
    pub auto_save_templates: bool,
    #[serde(default)]
    pub output_organization: OutputOrganization,
    #[serde(default = "default_daz_folders")]
    pub daz_folders: Vec<String>,
    #[serde(default)]
    pub inspector_width: Option<u32>,
}

pub fn default_daz_folders() -> Vec<String> {
    [
        "aniBlocks",
        "data",
        "Environments",
        "General",
        "Light Presets",
        "People",
        "Props",
        "Render Presets",
        "Render Settings",
        "Runtime",
        "Scene Builder",
        "Scenes",
        "Scripts",
        "Shader Presets",
        "Shaders",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect()
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            output_organization: OutputOrganization::Flat,
            language: "de".into(),
            theme: "dark".into(),
            check_updates: false,
            default_store: "DAZ 3D".into(),
            default_tags: vec!["DAZStudio4_5".into()],
            stores: [
                ("DAZ 3D", "IM"),
                ("Renderosity", "RO"),
                ("Renderhub", "RH"),
                ("Renderotica", "RE"),
                ("CGBytes", "CB"),
                ("CGTrader", "CG"),
                ("DeviantArt", "DA"),
                ("ShareCG", "SH"),
                ("Sketchfab", "SF"),
                ("Free3D", "F3D"),
                ("Turbosquid", "TS"),
                ("3DExport", "D3X"),
                ("Patreon", "PR"),
                ("Forender", "FR"),
                ("LOCAL USER", "LOCAL"),
            ]
            .into_iter()
            .map(|(name, prefix)| Store {
                name: name.into(),
                prefix: prefix.into(),
            })
            .collect(),
            tags: [
                "3dsMax",
                "Blender",
                "Bryce",
                "CarraraLegacy",
                "Carrara7",
                "Carrara7_2",
                "Carrara8",
                "Carrara8_5",
                "Cinema4D",
                "CloudAvailable",
                "CloudInstalled",
                "DAZStudioLegacy",
                "DAZStudio3",
                "DAZStudio4",
                "DAZStudio4_5",
                "DAZStudio5",
                "DSON_Poser",
                "General",
                "Hexagon",
                "InstallManager",
                "Lightwave",
                "Mac32",
                "Mac64",
                "Maya",
                "Photoshop",
                "PoserLegacy",
                "Poser9",
                "PrivateBuild",
                "PublicBuild",
                "PublishingBuild",
                "Unity",
                "Unreal",
                "Vue",
                "Win32",
                "Win64",
            ]
            .into_iter()
            .map(str::to_owned)
            .collect(),
            output_dir: None,
            template_dir: None,
            auto_save_templates: false,
            daz_folders: default_daz_folders(),
            inspector_width: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct SettingsIssue {
    pub tab: String,
    pub index: usize,
    pub field: String,
    #[serde(flatten)]
    pub error: AppError,
    pub severity: Severity,
}

impl Settings {
    pub fn normalized(&self) -> Self {
        let mut next = self.clone();
        next.template_dir = self
            .template_dir
            .as_ref()
            .map(|path| path.trim().to_owned())
            .filter(|path| !path.is_empty());
        for store in &mut next.stores {
            store.name = store.name.trim().into();
            store.prefix = store.prefix.trim().to_uppercase();
        }
        next.tags = next.tags.iter().map(|tag| tag.trim().to_owned()).collect();
        next.default_store = next
            .stores
            .iter()
            .find(|store| store.name.to_lowercase() == self.default_store.trim().to_lowercase())
            .map(|store| store.name.clone())
            .unwrap_or_else(|| self.default_store.trim().to_owned());
        next.default_tags = self
            .default_tags
            .iter()
            .map(|value| {
                next.tags
                    .iter()
                    .find(|tag| tag.to_lowercase() == value.trim().to_lowercase())
                    .cloned()
                    .unwrap_or_else(|| value.trim().to_owned())
            })
            .collect();
        next
    }

    pub fn issues(&self) -> Vec<SettingsIssue> {
        let settings = self.normalized();
        let mut issues = Vec::new();
        let mut add = |tab: &str, index, field: &str, code: &str, severity| {
            issues.push(SettingsIssue {
                tab: tab.into(),
                index,
                field: field.into(),
                error: AppError::new(code),
                severity,
            });
        };
        if self.inspector_width.is_some_and(|width| width < 300) {
            add(
                "general",
                0,
                "inspectorWidth",
                "invalidInspectorWidth",
                Severity::Error,
            );
        }
        if settings
            .template_dir
            .as_ref()
            .is_some_and(|path| !std::path::Path::new(path).is_absolute())
        {
            add(
                "general",
                0,
                "templateDir",
                "invalidAbsolutePath",
                Severity::Error,
            );
        }
        for (field, value) in [("language", &self.language), ("theme", &self.theme)] {
            if value.trim().is_empty() {
                add("general", 0, field, "required", Severity::Error);
            }
        }
        let mut names = HashSet::new();
        for (index, store) in settings.stores.iter().enumerate() {
            if store.name.is_empty() {
                add(
                    "stores",
                    index,
                    "name",
                    "catalogNameRequired",
                    Severity::Error,
                );
            } else if !names.insert(store.name.to_lowercase()) {
                add(
                    "stores",
                    index,
                    "name",
                    "catalogNameDuplicate",
                    Severity::Error,
                );
            }
            if !valid_prefix(&store.prefix) {
                add("stores", index, "prefix", "invalidPrefix", Severity::Error);
            } else if crate::package::DAZ_PREFIXES.contains(&store.prefix.as_str())
                && !store.name.eq_ignore_ascii_case("DAZ 3D")
            {
                add(
                    "stores",
                    index,
                    "prefix",
                    "reservedPrefix",
                    Severity::Warning,
                );
            }
        }
        let mut tags = HashSet::new();
        if settings.stores.is_empty() {
            add("stores", 0, "name", "storeCatalogEmpty", Severity::Error);
        } else if !names.contains(&settings.default_store.to_lowercase()) {
            add(
                "stores",
                0,
                "default",
                "defaultStoreInvalid",
                Severity::Error,
            );
        }
        for (index, tag) in settings.tags.iter().enumerate() {
            let code = if tag.is_empty() {
                Some("catalogTagRequired")
            } else if tag.eq_ignore_ascii_case("plugin") || tag.eq_ignore_ascii_case("software") {
                Some("catalogTagUnsupported")
            } else if !valid_tag(tag) {
                Some("invalidTags")
            } else if !tags.insert(tag.to_lowercase()) {
                Some("catalogTagDuplicate")
            } else {
                None
            };
            if let Some(code) = code {
                add("tags", index, "tag", code, Severity::Error);
            }
        }
        let mut folders = HashSet::new();
        let mut default_tags = HashSet::new();
        for tag in &settings.default_tags {
            let key = tag.to_lowercase();
            let code = if !tags.contains(&key) {
                Some("defaultTagInvalid")
            } else if !default_tags.insert(key) {
                Some("defaultTagDuplicate")
            } else {
                None
            };
            if let Some(code) = code {
                let index = settings
                    .tags
                    .iter()
                    .position(|value| value == tag)
                    .unwrap_or(0);
                add("tags", index, "default", code, Severity::Error);
            }
        }
        for (index, folder) in settings.daz_folders.iter().enumerate() {
            if validate_segment(folder).is_err() {
                add(
                    "dazFolders",
                    index,
                    "folder",
                    "invalidFolderName",
                    Severity::Error,
                );
            } else if !folders.insert(case_key(folder)) {
                add(
                    "dazFolders",
                    index,
                    "folder",
                    "dazFolderDuplicate",
                    Severity::Error,
                );
            }
        }
        issues
    }

    pub(crate) fn validate(&self) -> Result<(), AppError> {
        match self
            .issues()
            .into_iter()
            .find(|issue| issue.severity == Severity::Error)
        {
            Some(issue) => Err(issue
                .error
                .param("field", issue.field)
                .param("index", issue.index)),
            None => Ok(()),
        }
    }
}

pub fn valid_prefix(prefix: &str) -> bool {
    prefix.len() <= 7
        && prefix
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_uppercase)
        && prefix
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
}

pub fn valid_tag(tag: &str) -> bool {
    !tag.is_empty()
        && tag.trim() == tag
        && !tag.contains(',')
        && !tag.chars().any(char::is_control)
        && !tag.eq_ignore_ascii_case("Plugin")
        && !tag.eq_ignore_ascii_case("Software")
}

pub(crate) fn check_id(id: &str) -> Result<(), AppError> {
    if Uuid::parse_str(id).is_ok_and(|uuid| uuid.to_string() == id) {
        Ok(())
    } else {
        Err("invalidWorkspaceId".into())
    }
}
#[cfg(test)]
mod architecture_tests {
    use super::*;
    #[test]
    fn product_defaults_are_explicit_and_required() {
        let json = serde_json::to_value(Settings::default()).unwrap();
        assert_eq!(json["defaultStore"], "DAZ 3D");
        assert_eq!(json["defaultTags"], serde_json::json!(["DAZStudio4_5"]));
        for field in ["defaultStore", "defaultTags"] {
            let mut incomplete = json.clone();
            incomplete.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<Settings>(incomplete).is_err());
        }
    }

    #[test]
    fn product_default_references_are_normalized_and_validated() {
        let mut json = serde_json::to_value(Settings::default()).unwrap();
        json["stores"] = serde_json::json!([{"name":" Ärt Store ","prefix":" art "}]);
        json["tags"] = serde_json::json!([" General ", "DAZStudio4_5"]);
        json["defaultStore"] = " ärt store ".into();
        json["defaultTags"] = serde_json::json!([" general ", "dazstudio4_5"]);
        let settings: Settings = serde_json::from_value(json.clone()).unwrap();
        assert!(settings.validate().is_ok());
        let normalized = serde_json::to_value(settings.normalized()).unwrap();
        assert_eq!(normalized["defaultStore"], "Ärt Store");
        assert_eq!(
            normalized["defaultTags"],
            serde_json::json!(["General", "DAZStudio4_5"])
        );
        for (field, value, code) in [
            (
                "defaultStore",
                serde_json::json!("Missing"),
                "defaultStoreInvalid",
            ),
            (
                "defaultTags",
                serde_json::json!(["Missing"]),
                "defaultTagInvalid",
            ),
            (
                "defaultTags",
                serde_json::json!(["General", " general "]),
                "defaultTagDuplicate",
            ),
            ("stores", serde_json::json!([]), "storeCatalogEmpty"),
        ] {
            let mut invalid = json.clone();
            invalid[field] = value;
            let invalid: Settings = serde_json::from_value(invalid).unwrap();
            assert!(
                invalid
                    .issues()
                    .iter()
                    .any(|issue| issue.error.code == code),
                "{code}"
            );
            assert!(invalid.validate().is_err());
        }
        json["defaultTags"] = serde_json::json!([]);
        assert!(
            serde_json::from_value::<Settings>(json)
                .unwrap()
                .validate()
                .is_ok()
        );
    }

    #[test]
    fn output_organization_defaults_for_legacy_settings_and_round_trips() {
        let mut legacy = serde_json::to_value(Settings::default()).unwrap();
        legacy.as_object_mut().unwrap().remove("outputOrganization");
        legacy["language"] = "en".into();
        let restored: Settings = serde_json::from_value(legacy).unwrap();
        let json = serde_json::to_value(&restored).unwrap();
        assert_eq!(json["outputOrganization"], "flat");
        for mode in ["flat", "byDate"] {
            let mut json = json.clone();
            json["outputOrganization"] = mode.into();
            let settings: Settings = serde_json::from_value(json).unwrap();
            let temporary = tempfile::tempdir().unwrap();
            let workspace = crate::workspace::Workspace::new(temporary.path().join("app")).unwrap();
            workspace.save_settings(&settings).unwrap();
            let saved = serde_json::to_value(workspace.settings().unwrap()).unwrap();
            assert_eq!(saved["outputOrganization"], mode);
            assert_eq!(saved["language"], "en");
        }
    }
    #[test]
    fn settings_accept_registered_identifiers_without_backend_allowlists() {
        let settings = Settings {
            language: "fr-CA".into(),
            theme: "high-contrast".into(),
            ..Settings::default()
        };
        assert!(settings.validate().is_ok());
    }

    #[test]
    fn settings_validation_normalizes_and_reports_actual_catalog_indices() {
        let settings = Settings {
            stores: vec![
                Store {
                    name: " Custom ".into(),
                    prefix: " ro ".into(),
                },
                Store {
                    name: "custom".into(),
                    prefix: "bad!".into(),
                },
            ],
            tags: vec![" General ".into(), "general".into()],
            daz_folders: vec!["CON .txt".into(), "People".into(), "people".into()],
            ..Settings::default()
        };
        let normalized = settings.normalized();
        assert_eq!(normalized.stores[0].name, "Custom");
        assert_eq!(normalized.stores[0].prefix, "RO");
        assert_eq!(normalized.tags[0], "General");
        let issues = settings.issues();
        for (tab, index, code) in [
            ("stores", 1, "catalogNameDuplicate"),
            ("stores", 1, "invalidPrefix"),
            ("tags", 1, "catalogTagDuplicate"),
            ("dazFolders", 0, "invalidFolderName"),
            ("dazFolders", 2, "dazFolderDuplicate"),
        ] {
            assert!(
                issues.iter().any(|issue| issue.tab == tab
                    && issue.index == index
                    && issue.error.code == code)
            );
        }
    }

    #[test]
    fn normalized_settings_survive_storage_and_keep_unknown_identifiers() {
        let temporary = tempfile::tempdir().unwrap();
        let workspace = crate::workspace::Workspace::new(temporary.path().join("app")).unwrap();
        let mut settings = Settings {
            language: "fr-CA".into(),
            theme: "contrast".into(),
            ..Settings::default()
        };
        settings.stores[0].prefix = " im ".into();
        let saved = workspace.save_settings(&settings).unwrap();
        assert_eq!(saved.stores[0].prefix, "IM");
        assert_eq!(workspace.settings().unwrap(), saved);
        assert_eq!(workspace.settings().unwrap().language, "fr-CA");
    }
}
