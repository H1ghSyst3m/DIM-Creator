import type { ExportApproval, ExportRow, Package, Preview, Store, OutputDirectory } from './api'
import type { Translate } from './messages'

export function displaySku(sku: string, t: Translate) {
  return sku.trim() ? t('skuValue', {sku}) : t('skuMissing')
}

export function matchesStore(store: Store, query: string) {
  const term = query.trim().toLowerCase()
  return store.name.toLowerCase().includes(term) || store.prefix.toLowerCase().includes(term)
}

export function displayPart(part: string) {
  return /^\d{1,2}$/.test(part) ? part.padStart(2, '0') : '—'
}

export function filterPackages<T extends Pick<Package, 'name' | 'sku' | 'part'>>(items: T[], query: string) {
  const term = query.trim().toLowerCase()
  return items.filter(({ name, sku, part }) =>
    name.toLowerCase().includes(term) || sku.includes(term) || displayPart(part).includes(term))
}

export function markVisible(selected: ReadonlySet<string>, visible: Pick<Package, 'id'>[], checked: boolean) {
  const next = new Set(selected)
  for (const { id } of visible) {
    if (checked) next.add(id)
    else next.delete(id)
  }
  return next
}

export function selectPackages(selected: ReadonlySet<string>, visible: string[], anchor: string | null, id: string, mode: 'toggle' | 'range' | 'addRange') {
  const next = new Set(selected)
  if (mode === 'toggle') {
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  }
  if (mode === 'range') visible.forEach(value => next.delete(value))
  const start = anchor ? visible.indexOf(anchor) : -1
  const end = visible.indexOf(id)
  const range = start < 0 ? [id] : visible.slice(Math.min(start, end), Math.max(start, end) + 1)
  range.forEach(value => next.add(value))
  return next
}

export function packageTargets(selected: ReadonlySet<string>, order: string[], id: string) {
  return selected.has(id) ? order.filter(value => selected.has(value)) : [id]
}

export function nextPackageAfterRemoval(order: string[], active: string | null, removed: ReadonlySet<string>) {
  if (active && !removed.has(active)) return active
  const index = active ? order.indexOf(active) : -1
  return order.slice(index + 1).find(id => !removed.has(id)) ?? order.slice(0, index).reverse().find(id => !removed.has(id)) ?? null
}

export async function removePackages(ids: string[], remove: (id: string) => Promise<void>) {
  const failures: { id: string; error: unknown }[] = []
  for (const id of ids) {
    try { await remove(id) }
    catch (error) { failures.push({ id, error }) }
  }
  return failures
}

export function validExportIds(rows: { id: string; issues: Pick<ExportRow['issues'][number], 'severity'>[] }[]) {
  return rows.filter(row => row.issues.every(issue => issue.severity !== 'error')).map(row => row.id)
}

export function reviewedExport(rows: ExportRow[], approved: ReadonlySet<string>) {
  const valid = new Set(validExportIds(rows))
  const ids: string[] = []
  const replace: ExportApproval[] = []
  for (const { id, existing, outputPath, stamp } of rows) {
    if (!valid.has(id) || !outputPath) continue
    if (existing) {
      if (!approved.has(id) || !stamp) continue
      replace.push({ id, outputPath, stamp })
    }
    ids.push(id)
  }
  return { ids, replace }
}

export function excludedBy(item: Pick<Package, 'cleanSupport' | 'excluded'>, path: string) {
  const key = path.toLowerCase()
  if (item.cleanSupport && (key === 'runtime/support' || key.startsWith('runtime/support/'))) return 'support'
  if (item.excluded.some(value => key.startsWith(value.toLowerCase() + '/'))) return 'ancestor'
  if (item.excluded.some(value => value.toLowerCase() === key)) return 'self'
  return null
}

export function workspacePreviews(previews: Record<string, Preview>, outputs: Record<string, OutputDirectory>): Record<string, Preview> {
  // Native directory keys come from Rust; DIM filenames are ASCII.
  const key = (id: string, preview: Preview) => outputs[id] && preview.filename
    ? JSON.stringify([outputs[id].key, preview.filename.toUpperCase()]) : null
  const counts = new Map<string, number>()
  for (const [id, preview] of Object.entries(previews)) {
    const target = key(id, preview)
    if (target) counts.set(target, (counts.get(target) ?? 0) + 1)
  }
  return Object.fromEntries(Object.entries(previews).map(([id, preview]) => [id,
    (counts.get(key(id, preview) ?? '') ?? 0) > 1
      ? { ...preview, issues: [...preview.issues, { code: 'duplicateWorkspaceOutput', field: 'output', severity: 'warning' as const, params: {}, details: null, causes: [] }] }
      : preview,
  ]))
}
