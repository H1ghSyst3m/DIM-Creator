import type { WorklistEntry, WorklistSource } from './api'

export function entryIds(entry: WorklistEntry): string[] {
  return entry.kind === 'package' ? [entry.id] : entry.members
}

export function canNudge(entries: WorklistEntry[], source: WorklistSource, up: boolean): boolean {
  let siblings: string[]
  let ids: string[]
  if (source.kind === 'group') { siblings = entries.map(entry => entry.id); ids = [source.id] }
  else {
    ids = source.ids
    if (!ids.length) return false
    const parent = entries.find(entry => entryIds(entry).includes(ids[0]))
    if (!parent) return false
    if (parent.kind === 'group') {
      if (!ids.every(id => parent.members.includes(id))) return false
      siblings = parent.members
    } else {
      if (!ids.every(id => entries.some(entry => entry.kind === 'package' && entry.id === id))) return false
      siblings = entries.map(entry => entry.id)
    }
  }
  const selected = new Set(ids)
  return siblings.some((id, index) => selected.has(id) && siblings[index + (up ? -1 : 1)] !== undefined && !selected.has(siblings[index + (up ? -1 : 1)]))
}

export function canGroup(entries: WorklistEntry[], ids: string[]): boolean {
  return ids.length > 1 && !entries.some(entry => entry.kind === 'group' && entry.members.length === ids.length && entry.members.every(id => ids.includes(id)))
}
