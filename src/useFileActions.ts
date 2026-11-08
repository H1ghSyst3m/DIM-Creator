import { useState } from 'react'
import { api, type CopyConflict, type FileMutation, type TransferMode } from './api'
import { appError } from './messages'
import type { useWorkspace } from './useWorkspace'

export type FileConflict = { id: string; folder: string; sources: string[]; conflicts: CopyConflict[]; mode?: TransferMode }

/** Called under the application action gate; explorer selection/navigation stay in the UI. */
export function useFileActions(workspace: ReturnType<typeof useWorkspace>, options: {
  onConflict: (conflict: FileConflict) => void
  onMutation: (result: FileMutation) => void
  onError: (error: unknown) => void
}) {
  const [revision, setRevision] = useState(0)
  const [mutation, setMutation] = useState<FileMutation>()
  const refresh = (id: string) => {
    setRevision(current => current + 1)
    void workspace.flush().then(() => workspace.refresh(id)).catch(options.onError)
  }
  const apply = (result: FileMutation) => {
    workspace.applyFiles(result)
    setMutation(result)
    options.onMutation(result)
    setRevision(current => current + 1)
    const failures = result.results.filter(row => row.status !== 'success')
    if (result.saveError) options.onError(result.saveError)
    else {
      if (failures.length) options.onError(appError('fileOperationPartial', {}, failures.map(row =>
        appError('fileResultDetail', {path: row.path}, [row.message ?? appError('skip')]))))
      void workspace.refresh(result.package.id).catch(options.onError)
    }
  }
  const copy = async (id: string, folder: string, sources: string[]) => {
    await workspace.flush()
    const conflicts = await api.copyConflicts(id, folder, sources)
    if (conflicts.length) options.onConflict({id, folder, sources, conflicts})
    else { await api.copyFiles(id, folder, sources, 'cancel'); refresh(id) }
  }
  const transfer = async (id: string, folder: string, paths: string[], mode: TransferMode) => {
    await workspace.flush()
    const conflicts = await api.transferConflicts(id, folder, paths, mode)
    if (conflicts.length) options.onConflict({id, folder, sources:paths, conflicts, mode})
    else apply(await api.transferFiles(id, folder, paths, mode, 'cancel'))
  }
  const resolveConflict = async (draft: FileConflict, replace: boolean) => {
    await workspace.flush()
    const policy = replace ? 'replace' : 'skip'
    const approved = replace ? draft.conflicts : []
    if (draft.mode) apply(await api.transferFiles(draft.id, draft.folder, draft.sources, draft.mode, policy, approved))
    else { await api.copyFiles(draft.id, draft.folder, draft.sources, policy, approved); refresh(draft.id) }
  }
  const rename = async (id: string, path: string, name: string) => {
    await workspace.flush()
    apply(await api.renameEntry(id, path, name))
  }
  const trash = async (id: string, paths: string[]) => {
    await workspace.flush()
    apply(await api.trashEntries(id, paths))
  }
  const createFolder = async (id: string, folder: string, name: string) => {
    await workspace.flush()
    await api.createFolder(id, folder, name)
    refresh(id)
  }
  return { revision, mutation, reset: () => setMutation(undefined), refresh, copy, transfer, resolveConflict, rename, trash, createFolder }
}
