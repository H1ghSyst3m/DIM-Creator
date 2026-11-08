import type { FileChange } from './api'

export function selectFiles(selected: ReadonlySet<string>, anchor: string | null, path: string, order: string[], ctrl: boolean, shift: boolean) {
  const paths = ctrl ? new Set(selected) : new Set<string>()
  if (shift && anchor && order.includes(anchor)) {
    const start = order.indexOf(anchor), end = order.indexOf(path)
    for (const entry of order.slice(Math.min(start, end), Math.max(start, end) + 1)) paths.add(entry)
    return {paths, anchor}
  }
  if (ctrl && paths.has(path)) paths.delete(path)
  else paths.add(path)
  return {paths, anchor:path}
}

export function containsPath(parent: string, path: string) {
  return path.toUpperCase() === parent.toUpperCase() || path.toUpperCase().startsWith(parent.toUpperCase() + '/')
}

export function remapFilePath(path: string, changes: FileChange[]) {
  const change = changes.filter(change => change.kind !== 'copy' && containsPath(change.from, path)).sort((a,b) => b.from.length - a.from.length)[0]
  return !change ? path : change.to === null ? change.from.split('/').slice(0,-1).join('/') : change.to + path.slice(change.from.length)
}
