//! One-time metadata transfer. File ownership and persistence belong to Workspace.
use crate::{
    error::{AppError, Issue},
    model::Package,
    package::metadata_preview,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub enum SyncField {
    Store,
    Sku,
    Tags,
    Name,
    Cover,
    Support,
}

#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct SyncExpected {
    pub revision: u32,
    pub worklist_stamp: String,
    pub packages: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
pub struct SyncGroup {
    pub group: String,
    pub packages: Vec<Package>,
    pub expected: SyncExpected,
}

#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
pub struct SyncRequest {
    pub group: String,
    pub source: String,
    pub targets: Vec<String>,
    pub fields: Vec<SyncField>,
    pub order: Option<Vec<String>>,
    pub expected: SyncExpected,
}

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct SyncChange {
    pub before: Package,
    pub after: Package,
    pub fields: Vec<SyncField>,
    pub part_changed: bool,
    pub old_filename: Option<String>,
    pub new_filename: Option<String>,
    pub issues: Vec<Issue>,
}

impl SyncChange {
    pub fn between(before: &Package, after: Package, cover_changed: bool) -> Self {
        let fields = [
            (
                SyncField::Store,
                before.store != after.store
                    || before.prefix != after.prefix
                    || before.auto_prefix != after.auto_prefix,
            ),
            (SyncField::Sku, before.sku != after.sku),
            (SyncField::Tags, before.tags != after.tags),
            (SyncField::Name, before.name != after.name),
            (SyncField::Cover, cover_changed),
            (
                SyncField::Support,
                before.clean_support != after.clean_support,
            ),
        ]
        .into_iter()
        .filter_map(|(field, changed)| changed.then_some(field))
        .collect();
        let preview = metadata_preview(&after);
        Self {
            fields,
            part_changed: before.part != after.part,
            old_filename: metadata_preview(before).filename,
            new_filename: preview.filename,
            issues: preview.issues,
            before: before.clone(),
            after,
        }
    }
    pub fn changed(&self) -> bool {
        !self.fields.is_empty() || self.part_changed
    }
}

pub fn plan_changes(
    packages: &[Package],
    source: &str,
    targets: &[String],
    fields: &[SyncField],
    order: Option<&[String]>,
) -> Result<Vec<SyncChange>, AppError> {
    let source = packages
        .iter()
        .find(|p| p.id == source)
        .ok_or("invalidSyncAction")?;
    let ids: HashSet<_> = packages.iter().map(|p| &p.id).collect();
    if targets
        .iter()
        .any(|id| !ids.contains(id) || id == &source.id)
        || targets.iter().collect::<HashSet<_>>().len() != targets.len()
    {
        return Err("invalidSyncAction".into());
    }
    if let Some(order) = order {
        if order.len() > 99 {
            return Err("syncPartLimit".into());
        }
        if order.len() != ids.len() || order.iter().collect::<HashSet<_>>() != ids {
            return Err("invalidSyncAction".into());
        }
    }
    let mut changes = Vec::new();
    for before in packages {
        let mut after = before.clone();
        if targets.contains(&before.id) {
            for field in fields {
                match field {
                    SyncField::Store => {
                        after.store.clone_from(&source.store);
                        after.prefix.clone_from(&source.prefix);
                        after.auto_prefix = source.auto_prefix;
                    }
                    SyncField::Sku => after.sku.clone_from(&source.sku),
                    SyncField::Tags => after.tags.clone_from(&source.tags),
                    SyncField::Name => after.name.clone_from(&source.name),
                    SyncField::Support => after.clean_support = source.clean_support,
                    // Managed cover references may never be copied between package directories.
                    SyncField::Cover => {}
                }
            }
        }
        if let Some(order) = order {
            after.part = format!(
                "{:02}",
                order.iter().position(|id| id == &before.id).unwrap() + 1
            );
        }
        after.validate_storage(&before.id)?;
        let change = SyncChange::between(before, after, false);
        if change.changed() {
            changes.push(change);
        }
    }
    Ok(changes)
}
