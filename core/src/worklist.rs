use crate::{error::AppError, model::check_id};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, ts_rs::TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum WorklistEntry {
    Package {
        id: String,
    },
    Group {
        id: String,
        members: Vec<String>,
        collapsed: bool,
    },
}

impl WorklistEntry {
    pub fn id(&self) -> &str {
        match self {
            Self::Package { id } | Self::Group { id, .. } => id,
        }
    }
    pub fn ids(&self) -> &[String] {
        match self {
            Self::Package { id } => std::slice::from_ref(id),
            Self::Group { members, .. } => members,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, ts_rs::TS)]
pub struct Worklist {
    pub revision: u32,
    pub entries: Vec<WorklistEntry>,
}

#[derive(Debug, Clone, Serialize, ts_rs::TS)]
#[serde(rename_all = "camelCase")]
pub struct WorklistState {
    #[serde(flatten)]
    pub value: Worklist,
    pub can_undo: bool,
    pub save_error: Option<AppError>,
    pub warnings: Vec<AppError>,
}

#[derive(Debug, Serialize, ts_rs::TS)]
pub struct CreatedPackage {
    pub package: crate::model::Package,
    pub worklist: WorklistState,
}

#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum WorklistSource {
    Packages { ids: Vec<String> },
    Group { id: String },
}

#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum WorklistTarget {
    Root {
        anchor: Option<String>,
        before: bool,
    },
    Member {
        group: String,
        anchor: String,
        before: bool,
    },
    Join {
        group: String,
    },
    Combine {
        id: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, ts_rs::TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum WorklistAction {
    Group {
        ids: Vec<String>,
    },
    Detach {
        ids: Vec<String>,
    },
    Dissolve {
        group: String,
    },
    Collapse {
        group: String,
        collapsed: bool,
    },
    Move {
        source: WorklistSource,
        target: WorklistTarget,
    },
    Nudge {
        source: WorklistSource,
        up: bool,
    },
}

impl Worklist {
    pub fn ids(&self) -> Vec<String> {
        self.entries
            .iter()
            .flat_map(|entry| entry.ids().iter().cloned())
            .collect()
    }
    pub fn validate(&self) -> Result<(), AppError> {
        let mut packages = HashSet::new();
        let mut blocks = HashSet::new();
        for entry in &self.entries {
            check_id(entry.id())?;
            if !blocks.insert(entry.id()) {
                return Err("invalidWorklist".into());
            }
            if matches!(entry, WorklistEntry::Group { members, .. } if members.len() < 2) {
                return Err("invalidWorklist".into());
            }
            for id in entry.ids() {
                check_id(id)?;
                if !packages.insert(id) {
                    return Err("invalidWorklist".into());
                }
            }
        }
        if self
            .entries
            .iter()
            .any(|entry| matches!(entry, WorklistEntry::Group { id, .. } if packages.contains(id)))
        {
            return Err("invalidWorklist".into());
        }
        Ok(())
    }
    pub fn apply(&self, action: &WorklistAction) -> Result<Self, AppError> {
        let mut next = self.clone();
        match action {
            WorklistAction::Group { ids } => {
                let members = self.selection(ids)?;
                if members.len() < 2 {
                    return Err("invalidWorklistAction".into());
                }
                if self.entries.iter().any(|entry| matches!(entry, WorklistEntry::Group { members: current, .. } if *current == members)) { return Ok(next); }
                let position = self
                    .entries
                    .iter()
                    .position(|entry| entry.ids().iter().any(|id| members.contains(id)))
                    .unwrap();
                next.remove_members(&members);
                next.entries.insert(
                    position,
                    WorklistEntry::Group {
                        id: uuid::Uuid::new_v4().to_string(),
                        members,
                        collapsed: false,
                    },
                );
            }
            WorklistAction::Detach { ids } => {
                let members = self.selection(ids)?;
                next.entries = self
                    .entries
                    .iter()
                    .flat_map(|entry| {
                        let mut result = vec![entry.clone()];
                        if let WorklistEntry::Group {
                            members: current, ..
                        } = &mut result[0]
                        {
                            let detached: Vec<_> = current
                                .iter()
                                .filter(|id| members.contains(id))
                                .cloned()
                                .collect();
                            current.retain(|id| !members.contains(id));
                            result.extend(
                                detached.into_iter().map(|id| WorklistEntry::Package { id }),
                            );
                        }
                        result
                    })
                    .collect();
            }
            WorklistAction::Dissolve { group } => {
                let index = self.group_index(group)?;
                let entries = self.entries[index]
                    .ids()
                    .iter()
                    .cloned()
                    .map(|id| WorklistEntry::Package { id });
                next.entries.splice(index..=index, entries);
            }
            WorklistAction::Collapse { group, collapsed } => {
                let index = self.group_index(group)?;
                if let WorklistEntry::Group {
                    collapsed: current, ..
                } = &mut next.entries[index]
                {
                    *current = *collapsed;
                }
            }
            WorklistAction::Move { source, target } => next.move_entries(source, target)?,
            WorklistAction::Nudge { source, up } => match source {
                WorklistSource::Group { id } => {
                    self.group_index(id)?;
                    nudge(&mut next.entries, |entry| entry.id() == id, *up);
                }
                WorklistSource::Packages { ids } => {
                    let members = self.selection(ids)?;
                    let parent = self
                        .entries
                        .iter()
                        .find(|entry| entry.ids().contains(&members[0]))
                        .unwrap();
                    if let WorklistEntry::Group {
                        id,
                        members: children,
                        ..
                    } = parent
                    {
                        if !members.iter().all(|id| children.contains(id)) {
                            return Err("worklistMixedParents".into());
                        }
                        if let WorklistEntry::Group {
                            members: children, ..
                        } = &mut next.entries[self.group_index(id)?]
                        {
                            nudge(children, |id| members.contains(id), *up);
                        }
                    } else {
                        if members.iter().any(|id| !self.entries.iter().any(|entry| matches!(entry, WorklistEntry::Package { id: root } if root == id))) { return Err("worklistMixedParents".into()); }
                        nudge(
                            &mut next.entries,
                            |entry| members.iter().any(|id| id == entry.id()),
                            *up,
                        );
                    }
                }
            },
        }
        next.normalize();
        next.validate()?;
        Ok(next)
    }

    fn selection(&self, ids: &[String]) -> Result<Vec<String>, AppError> {
        let requested: HashSet<_> = ids.iter().collect();
        let ordered: Vec<_> = self
            .ids()
            .into_iter()
            .filter(|id| requested.contains(id))
            .collect();
        if ids.is_empty() || requested.len() != ids.len() || ordered.len() != ids.len() {
            return Err("invalidWorklistAction".into());
        }
        Ok(ordered)
    }

    fn group_index(&self, id: &str) -> Result<usize, AppError> {
        self.entries
            .iter()
            .position(
                |entry| matches!(entry, WorklistEntry::Group { id: current, .. } if current == id),
            )
            .ok_or_else(|| "invalidWorklistAction".into())
    }

    fn remove_members(&mut self, ids: &[String]) {
        // Keep empty blocks until insertion is complete so target positions remain stable.
        for entry in &mut self.entries {
            match entry {
                WorklistEntry::Package { id } if ids.contains(id) => {
                    *entry = WorklistEntry::Group {
                        id: id.clone(),
                        members: vec![],
                        collapsed: false,
                    }
                }
                WorklistEntry::Group { members, .. } => members.retain(|id| !ids.contains(id)),
                _ => {}
            }
        }
    }

    pub(crate) fn normalize(&mut self) {
        self.entries.retain(|entry| !entry.ids().is_empty());
        for entry in &mut self.entries {
            if let WorklistEntry::Group { members, .. } = entry
                && members.len() == 1
            {
                *entry = WorklistEntry::Package {
                    id: members[0].clone(),
                };
            }
        }
    }

    fn move_entries(
        &mut self,
        source: &WorklistSource,
        target: &WorklistTarget,
    ) -> Result<(), AppError> {
        let (members, block) = match source {
            WorklistSource::Packages { ids } => (self.selection(ids)?, None),
            WorklistSource::Group { id } => {
                let entry = self.entries[self.group_index(id)?].clone();
                (entry.ids().to_vec(), Some(entry))
            }
        };
        match target {
            WorklistTarget::Root { anchor, before } => {
                let index = match anchor {
                    Some(id) => {
                        let index = self
                            .entries
                            .iter()
                            .position(|entry| entry.id() == id)
                            .ok_or("invalidWorklistAction")?;
                        if block.as_ref().is_some_and(|entry| entry.id() == id)
                            || matches!(&self.entries[index], WorklistEntry::Package { id } if members.contains(id))
                        {
                            return Ok(());
                        }
                        index + usize::from(!before)
                    }
                    None => self.entries.len(),
                };
                self.remove_members(&members);
                let entries = block.map_or_else(
                    || {
                        members
                            .into_iter()
                            .map(|id| WorklistEntry::Package { id })
                            .collect()
                    },
                    |entry| vec![entry],
                );
                self.entries.splice(index..index, entries);
            }
            WorklistTarget::Join { group } | WorklistTarget::Member { group, .. } => {
                let index = self.group_index(group)?;
                if block.as_ref().is_some_and(|entry| entry.id() == group) {
                    return Ok(());
                }
                let current = self.entries[index].ids();
                let insertion = match target {
                    WorklistTarget::Member { anchor, before, .. } => {
                        if block.is_some() {
                            return Err("invalidWorklistAction".into());
                        }
                        let position = current
                            .iter()
                            .position(|id| id == anchor)
                            .ok_or("invalidWorklistAction")?;
                        if members.contains(anchor) {
                            return Ok(());
                        }
                        current[..position + usize::from(!before)]
                            .iter()
                            .filter(|id| !members.contains(id))
                            .count()
                    }
                    _ => {
                        if members.iter().all(|id| current.contains(id)) {
                            return Ok(());
                        }
                        current.iter().filter(|id| !members.contains(id)).count()
                    }
                };
                self.remove_members(&members);
                if let WorklistEntry::Group {
                    members: children,
                    collapsed,
                    ..
                } = &mut self.entries[index]
                {
                    children.splice(insertion..insertion, members);
                    *collapsed = false;
                }
            }
            WorklistTarget::Combine { id } => {
                if block.is_some() {
                    return Err("invalidWorklistAction".into());
                }
                let index = self.entries.iter().position(|entry| matches!(entry, WorklistEntry::Package { id: current } if current == id)).ok_or("invalidWorklistAction")?;
                if members.contains(id) {
                    return Ok(());
                }
                self.remove_members(&members);
                self.entries[index] = WorklistEntry::Group {
                    id: uuid::Uuid::new_v4().to_string(),
                    members: std::iter::once(id.clone()).chain(members).collect(),
                    collapsed: false,
                };
            }
        }
        Ok(())
    }
}

fn nudge<T>(items: &mut [T], selected: impl Fn(&T) -> bool, up: bool) {
    if up {
        for index in 1..items.len() {
            if selected(&items[index]) && !selected(&items[index - 1]) {
                items.swap(index, index - 1);
            }
        }
    } else {
        for index in (0..items.len().saturating_sub(1)).rev() {
            if selected(&items[index]) && !selected(&items[index + 1]) {
                items.swap(index, index + 1);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn id(n: usize) -> String {
        format!("00000000-0000-4000-8000-{n:012}")
    }
    fn list() -> Worklist {
        Worklist {
            revision: 0,
            entries: (1..=5)
                .map(|n| WorklistEntry::Package { id: id(n) })
                .collect(),
        }
    }
    #[test]
    fn groups_follow_list_order_and_moves_preserve_every_package() {
        let original = list();
        let grouped = original
            .apply(&WorklistAction::Group {
                ids: vec![id(4), id(2)],
            })
            .unwrap();
        assert_eq!(grouped.ids(), vec![id(1), id(2), id(4), id(3), id(5)]);
        assert!(
            matches!(&grouped.entries[1], WorklistEntry::Group{members,..} if members == &vec![id(2),id(4)])
        );
        let moved = grouped
            .apply(&WorklistAction::Move {
                source: WorklistSource::Packages { ids: vec![id(2)] },
                target: WorklistTarget::Root {
                    anchor: None,
                    before: false,
                },
            })
            .unwrap();
        assert_eq!(moved.ids(), vec![id(1), id(4), id(3), id(5), id(2)]);
        assert!(
            moved
                .entries
                .iter()
                .all(|entry| matches!(entry, WorklistEntry::Package { .. }))
        );
        assert_eq!(original.ids(), vec![id(1), id(2), id(3), id(4), id(5)]);
    }
    #[test]
    fn combining_merging_and_member_insertion_keep_target_identity() {
        let first = list()
            .apply(&WorklistAction::Move {
                source: WorklistSource::Packages { ids: vec![id(1)] },
                target: WorklistTarget::Combine { id: id(2) },
            })
            .unwrap();
        let second = first
            .apply(&WorklistAction::Group {
                ids: vec![id(3), id(4)],
            })
            .unwrap();
        let a = second.entries[0].id().to_owned();
        let b = second.entries[1].id().to_owned();
        let merged = second
            .apply(&WorklistAction::Move {
                source: WorklistSource::Group { id: b },
                target: WorklistTarget::Join { group: a.clone() },
            })
            .unwrap();
        assert_eq!(merged.entries[0].id(), a);
        assert_eq!(merged.ids(), vec![id(2), id(1), id(3), id(4), id(5)]);
        let sorted = merged
            .apply(&WorklistAction::Move {
                source: WorklistSource::Packages { ids: vec![id(4)] },
                target: WorklistTarget::Member {
                    group: a,
                    anchor: id(2),
                    before: true,
                },
            })
            .unwrap();
        assert_eq!(sorted.ids(), vec![id(4), id(2), id(1), id(3), id(5)]);
    }
    #[test]
    fn nudges_selected_runs_and_rejects_missing_or_duplicate_ids() {
        let shifted = list()
            .apply(&WorklistAction::Nudge {
                source: WorklistSource::Packages {
                    ids: vec![id(2), id(3), id(5)],
                },
                up: true,
            })
            .unwrap();
        assert_eq!(shifted.ids(), vec![id(2), id(3), id(1), id(5), id(4)]);
        for ids in [vec![id(99)], vec![id(1), id(1)], vec![]] {
            assert!(list().apply(&WorklistAction::Group { ids }).is_err());
        }
        let mut invalid = list();
        invalid.entries.push(WorklistEntry::Package { id: id(1) });
        assert!(invalid.validate().is_err());
    }
}
