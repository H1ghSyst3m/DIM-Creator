import { Channel, invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { open } from '@tauri-apps/plugin-dialog'
import { openUrl } from '@tauri-apps/plugin-opener'

export type * from './bindings'
import type { AppError, SettingsIssue, WorkspaceView, Package, Settings, FileEntry, CopyConflict, CopyPolicy, TransferMode, FileMutation, Preview as BackendPreview, OutputDirectory, OutputContext, ExportTarget, ExportPreflight, ExportApproval, JobEvent, Update } from './bindings'
import type { CreatedPackage, WorklistAction, WorklistState } from './bindings'

export type Preview = BackendPreview & { stale?: boolean }
import type { SyncLoad, SyncRequest, SyncPreview, SyncResult } from './bindings'
import type { ArchiveJobEvent, ImportDraft, ImportPreview } from './bindings'

export type NativeDropEvent =
  | {type:'enter'|'drop';paths:string[];x:number;y:number}
  | {type:'over';x:number;y:number}
  | {type:'leave'}

export const api = {
  chooseArchives: (title: string) => open({title,multiple:true,filters:[{name:'ZIP, RAR, 7z',extensions:['zip','rar','7z']}]}),
  analyzeArchives: (sources: string[], onEvent: (event: ArchiveJobEvent)=>void) => invoke<string>('analyze_archives',{sources,onEvent:new Channel<ArchiveJobEvent>(onEvent)}),
  previewArchiveImport: (sessionId: string,draft: ImportDraft) => invoke<ImportPreview>('preview_archive_import',{sessionId,draft}),
  startArchiveImport: (sessionId: string,planId: string,policy: CopyPolicy,onEvent: (event: ArchiveJobEvent)=>void) => invoke<string>('start_archive_import',{sessionId,planId,policy,onEvent:new Channel<ArchiveJobEvent>(onEvent)}),
  cancelArchiveJob: (jobId: string) => invoke<void>('cancel_archive_job',{jobId}),
  releaseArchiveSession: (sessionId: string) => invoke<void>('release_archive_session',{sessionId}),
  loadGroupSync: (group: string) => invoke<SyncLoad>('load_group_sync', {group}),
  previewGroupSync: (request: SyncRequest, context: OutputContext) => invoke<SyncPreview>('preview_group_sync', {request, context}),
  applyGroupSync: (request: SyncRequest, context: OutputContext, stamp: string) => invoke<SyncResult>('apply_group_sync', {request, context, stamp}),
  canClose: () => invoke<boolean>('can_close'),
  loadWorkspace: () => invoke<WorkspaceView>('load_workspace'),
  createPackage: (name: string) => invoke<CreatedPackage>('create_package', { name }),
  savePackage: (item: Package) => invoke<void>('save_package', { package: item }),
  removePackage: (id: string) => invoke<WorklistState>('remove_package', { id }),
  changeWorklist: (revision: number, action: WorklistAction) => invoke<WorklistState>('change_worklist', {revision, action}),
  undoWorklist: (revision: number) => invoke<WorklistState>('undo_worklist', {revision}),
  retryWorklist: () => invoke<WorklistState>('retry_worklist'),
  saveSettings: (settings: Settings) => invoke<Settings>('save_settings', { settings }),
  validateSettings: (settings: Settings) => invoke<SettingsIssue[]>('validate_settings', {settings}),
  validateEntryName: (name: string) => invoke<AppError | null>('validate_entry_name', {name}),
  listFiles: (id: string, folder: string) => invoke<FileEntry[]>('list_files', { id, folder }),
  createFolder: (id: string, folder: string, name: string) => invoke<void>('create_folder', { id, folder, name }),
  openFolder: (id: string, folder: string) => invoke<void>('open_folder', { id, folder }),
  copyConflicts: (id: string, folder: string, sources: string[]) =>
    invoke<CopyConflict[]>('copy_conflicts', { id, folder, sources }),
  copyFiles: (id: string, folder: string, sources: string[], policy: CopyPolicy, approved: CopyConflict[] = []) =>
    invoke<void>('copy_files', { id, folder, sources, policy, approved }),
  transferConflicts: (id: string, folder: string, paths: string[], mode: TransferMode) => invoke<CopyConflict[]>('transfer_conflicts', {id, folder, paths, mode}),
  transferFiles: (id: string, folder: string, paths: string[], mode: TransferMode, policy: CopyPolicy, approved: CopyConflict[] = []) => invoke<FileMutation>('transfer_files', {id, folder, paths, mode, policy, approved}),
  renameEntry: (id: string, path: string, name: string) => invoke<FileMutation>('rename_entry', {id, path, name}),
  trashEntries: (id: string, paths: string[]) => invoke<FileMutation>('trash_entries', {id, paths}),
  setCover: (id: string, source: string | null) => invoke<Package>('set_cover', { id, source }),
  getCover: (id: string) => invoke<string | null>('get_cover', { id }),
  preview: (id: string) => invoke<Preview>('preview_package', { id }),
  previewOutputs: (ids: string[]) => invoke<OutputDirectory[]>('preview_outputs', {ids}),
  preflight: (ids: string[], outputDir: string) => invoke<ExportPreflight>('preflight_export', { ids, outputDir }),
  startExport: (context: OutputContext, targets: ExportTarget[], replace: ExportApproval[], onEvent: (event: JobEvent) => void) => {
    const channel = new Channel<JobEvent>(onEvent)
    return invoke<string>('start_export', { context, targets, replace, onEvent: channel })
  },
  cancelExport: (jobId: string) => invoke<void>('cancel_export', { jobId }),
  checkUpdates: () => invoke<Update | null>('check_updates'),
  chooseOutput: (title: string) => open({ directory: true, title }),
  chooseSources: (title: string, directory: boolean) => open({ title, directory, multiple: true }),
  chooseCover: (title: string) => open({ title, filters: [{ name: 'JPEG, PNG, WebP', extensions: ['jpg', 'jpeg', 'png', 'webp'] }] }),
  onDrop: (onDrop: (event: NativeDropEvent) => void) =>
    getCurrentWebview().onDragDropEvent(({ payload }) => {
      if (payload.type === 'leave') return onDrop({type:'leave'})
      const position={x:payload.position.x/window.devicePixelRatio,y:payload.position.y/window.devicePixelRatio}
      onDrop(payload.type==='over'?{type:'over',...position}:{type:payload.type,paths:payload.paths,...position})
    }),
  openUrl,
}
