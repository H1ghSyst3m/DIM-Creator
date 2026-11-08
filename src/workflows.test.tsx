// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type JobEvent, type OutputDirectory, type SyncSession } from './api'
import { useWorkspace } from './useWorkspace'
import { useExport, type ExportDraft } from './useExport'
import { useAppActions } from './useAppActions'
import { useFileActions } from './useFileActions'
import { useArchiveImport } from './useArchiveImport'
import type { ArchiveJobEvent } from './api'
import { appError, errorText, translator } from './messages'
import { deferred, mount, testPackage, testSettings } from './testSupport'
import { themes } from './themes'

let dispose: (() => void) | undefined
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.spyOn(api,'loadWorkspace').mockResolvedValue({packages:[testPackage],settings:testSettings,warnings:[],worklist:{revision:0,entries:[{kind:'package',id:'test'}],canUndo:false,saveError:null,warnings:[]}})
  vi.spyOn(api,'previewOutputs').mockResolvedValue([])
  vi.spyOn(api,'preview').mockResolvedValue({filename:'test.zip',entries:[],size:0,issues:[]})
  vi.spyOn(api,'savePackage').mockResolvedValue()
})
afterEach(() => { dispose?.(); dispose=undefined; vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('cancels archive analysis requested before the job ID arrives and ignores late events', async () => {
  let imports!: ReturnType<typeof useArchiveImport>
  function Probe() {imports=useArchiveImport(useWorkspace(),vi.fn());return null}
  dispose=(await mount(<Probe/>)).unmount
  const started=deferred<string>()
  let receive!: (event:ArchiveJobEvent)=>void
  vi.spyOn(api,'analyzeArchives').mockImplementation((_sources,callback)=>{receive=callback;return started.promise})
  const cancel=vi.spyOn(api,'cancelArchiveJob').mockResolvedValue()
  let running!:Promise<void>
  await act(async()=>{running=imports.analyze(['test.zip'])})
  expect(imports.isRunning()).toBe(true)
  await act(async()=>imports.cancel())
  expect(cancel).not.toHaveBeenCalled()
  await act(async()=>{started.resolve('analysis');await running})
  expect(cancel).toHaveBeenCalledExactlyOnceWith('analysis')
  const finished:ArchiveJobEvent={jobId:'analysis',progress:{phase:'inventory',name:'test.zip',entries:0,bytes:0},finished:true,session:null,result:null,error:appError('cancelled')}
  await act(async()=>receive(finished))
  expect(imports.isRunning()).toBe(false)
  await act(async()=>imports.close())
  await act(async()=>receive({...finished,session:{id:'late',contents:[],templateDirectoryIssue:null},error:null}))
  expect(imports.session).toBeUndefined()
})

it('adopts completed archive results without content scans and does not apply them twice', async () => {
  let imports!: ReturnType<typeof useArchiveImport>, workspace!:ReturnType<typeof useWorkspace>
  const imported=vi.fn()
  function Probe() {workspace=useWorkspace();imports=useArchiveImport(workspace,imported);return null}
  dispose=(await mount(<Probe/>)).unmount
  let receive!: (event:ArchiveJobEvent)=>void
  vi.spyOn(api,'analyzeArchives').mockImplementation(async(_sources,callback)=>{receive=callback;return 'analysis'})
  await act(async()=>imports.analyze(['test.zip']))
  const event:ArchiveJobEvent={jobId:'analysis',progress:{phase:'recognize',name:'test.zip',entries:1,bytes:3},finished:true,session:{id:'session',contents:[],templateDirectoryIssue:null},result:null,error:null}
  await act(async()=>receive(event))
  vi.spyOn(api,'startArchiveImport').mockImplementation(async(_session,_plan,_policy,callback)=>{receive=callback;return 'import'})
  await act(async()=>imports.start({id:'plan',templates:[],targets:[],groups:[],skipped:0},'skip'))
  const calls=vi.mocked(api.preview).mock.calls.length
  const result={templates:[],results:[],packages:[{...testPackage,name:'Imported'}],worklist:{revision:0,entries:[{kind:'package' as const,id:'test'}],canUndo:false,saveError:null,warnings:[]}}
  await act(async()=>receive({...event,jobId:'import',session:null,result}))
  await act(async()=>receive({...event,jobId:'import',session:null,result}))
  expect(imported).toHaveBeenCalledTimes(1)
  expect(workspace.packages[0].name).toBe('Imported')
  expect(workspace.previews.test.stale).toBe(true)
  expect(api.preview).toHaveBeenCalledTimes(calls)
})

it('keeps package caches and worklist undo without an explorer refresh after a template-only import', async () => {
  let imports!: ReturnType<typeof useArchiveImport>, workspace!: ReturnType<typeof useWorkspace>
  const imported = vi.fn()
  function Probe() {workspace=useWorkspace();imports=useArchiveImport(workspace,imported);return null}
  dispose=(await mount(<Probe/>)).unmount
  await act(async()=>workspace.adoptWorklist({...workspace.worklist!,canUndo:true},true))
  const before={packages:workspace.packages,previews:workspace.previews,outputs:workspace.outputs,worklist:workspace.worklist}
  let receive!: (event:ArchiveJobEvent)=>void
  vi.spyOn(api,'analyzeArchives').mockImplementation(async(_sources,callback)=>{receive=callback;return 'analysis'})
  await act(async()=>imports.analyze(['Templates.zip']))
  const event:ArchiveJobEvent={jobId:'analysis',progress:{phase:'recognize',name:'Templates.zip',entries:1,bytes:3},finished:true,session:{id:'session',contents:[],templateDirectoryIssue:null},result:null,error:null}
  await act(async()=>receive(event))
  vi.spyOn(api,'startArchiveImport').mockImplementation(async(_session,_plan,_policy,callback)=>{receive=callback;return 'import'})
  await act(async()=>imports.start({id:'plan',templates:[],targets:[],groups:[],skipped:0},'cancel'))
  const scans=vi.mocked(api.preview).mock.calls.length
  const targets=vi.mocked(api.previewOutputs).mock.calls.length
  await act(async()=>receive({...event,jobId:'import',session:null,result:{packages:[],results:[],worklist:null,templates:[{archiveId:'archive',path:'C:/Templates/Templates.zip',status:'success',reason:null,error:null}]}}))
  expect(workspace.packages).toEqual(before.packages)
  expect(workspace.previews).toBe(before.previews)
  expect(workspace.outputs).toBe(before.outputs)
  expect(workspace.worklist).toBe(before.worklist)
  expect(workspace.worklist?.canUndo).toBe(true)
  expect(imported).not.toHaveBeenCalled()
  expect(api.preview).toHaveBeenCalledTimes(scans)
  expect(api.previewOutputs).toHaveBeenCalledTimes(targets)
  expect(imports.result?.templates[0].status).toBe('success')
})

it('adopts a reloaded source without scans and rejects an older in-flight content preview', async () => {
  let workspace!: ReturnType<typeof useWorkspace>
  function Probe() {workspace=useWorkspace();return null}
  dispose=(await mount(<Probe/>)).unmount
  const late=deferred<Awaited<ReturnType<typeof api.preview>>>()
  vi.mocked(api.preview).mockReturnValueOnce(late.promise)
  let refreshing!:Promise<void>
  await act(async()=>{refreshing=workspace.refresh('test')})
  const item={...testPackage,sku:'2'}
  const session:SyncSession={data:{group:'group',packages:[item],expected:{revision:0,worklistStamp:'stamp',packages:{test:'stamp'}}},context:{baseDir:'C:/Output',organization:'flat',localDate:'2026-11-05'},worklist:{revision:0,entries:[{kind:'package',id:'test'}],canUndo:false,warnings:[],saveError:null},metadata:[{id:'test',preview:{filename:'new.zip',issues:[],entries:[],size:0}}],outputs:[]}
  const calls=vi.mocked(api.preview).mock.calls.length
  await act(async()=>workspace.adoptSyncSession(session))
  await act(async()=>{late.resolve({filename:'old.zip',issues:[],entries:['old'],size:1});await refreshing})
  expect(workspace.packages[0].sku).toBe('2')
  expect(workspace.previews.test.filename).toBe('new.zip')
  expect(workspace.previews.test.stale).toBe(true)
  expect(api.preview).toHaveBeenCalledTimes(calls)
  await act(async()=>workspace.adoptSync({packages:[item],unavailable:[],worklist:session.worklist,error:null},{changes:[],order:null,stamp:'stamp',metadata:session.metadata,outputs:[],notices:[]}))
  expect(workspace.previews.test.filename).toBe('new.zip')
  expect(workspace.previews.test.stale).toBe(true)
  expect(workspace.worklist?.canUndo).toBe(false)
})

it('adopts saved metadata when the sync opener retains its pre-autosave callback', async () => {
  let workspace!: ReturnType<typeof useWorkspace>
  function Probe() {workspace=useWorkspace();return null}
  dispose=(await mount(<Probe/>)).unmount
  const item={...testPackage,name:'Pending edit'}
  await act(async()=>workspace.edit(item))
  const adopt=workspace.adoptSyncSession
  const session:SyncSession={data:{group:'group',packages:[item],expected:{revision:0,worklistStamp:'stamp',packages:{test:'stamp'}}},context:{baseDir:'C:/Output',organization:'flat',localDate:'2026-11-05'},worklist:{revision:0,entries:[{kind:'package',id:'test'}],canUndo:false,warnings:[],saveError:null},metadata:[{id:'test',preview:{filename:'pending.zip',issues:[],entries:[],size:0}}],outputs:[]}
  const calls=vi.mocked(api.preview).mock.calls.length
  await act(async()=>{await workspace.prepareMetadataAction();adopt(session)})
  expect(workspace.previews.test.filename).toBe('pending.zip')
  expect(workspace.previews.test.stale).toBe(true)
  expect(api.preview).toHaveBeenCalledTimes(calls)
})

it('locks mutations for an export independently of dialogs and ignores late job events', async () => {
  let state!: {workspace:ReturnType<typeof useWorkspace>; exports:ReturnType<typeof useExport>; actions:ReturnType<typeof useAppActions>}
  function Probe() {
    const workspace = useWorkspace(), exports = useExport(workspace), actions = useAppActions(workspace,exports.isRunning)
    state = {workspace,exports,actions}; return null
  }
  dispose = (await mount(<Probe />)).unmount
  const listeners: ((event:JobEvent) => void)[] = []
  vi.spyOn(api,'startExport').mockImplementation(async (_ids,_output,_replace,listener) => { listeners.push(listener); return `job${listeners.length}` })
  const draft: ExportDraft = {rows:[{id:'test',name:'Test',filename:'test.zip',outputDir:'C:/Output',outputPath:'C:/Output/test.zip',issues:[],existing:false,stamp:null}],context:{baseDir:'C:/Output',organization:'flat',localDate:'2026-01-09'}}
  await act(async () => state.actions.perform(() => state.exports.start(draft,['test'],[])))
  expect(state.exports.running).toBe(true)
  await expect(state.actions.perform(async () => {})).rejects.toMatchObject({code:'closeBusy'})
  const completion: JobEvent = {jobId:'job1',packageId:null,phase:'finished',done:1,total:1,results:[{id:'test',status:'success',path:'C:/Output/test.zip',message:null}]}
  await act(async () => listeners[0](completion))
  expect(state.actions.available()).toBe(true)
  await act(async () => state.actions.perform(() => state.exports.start(draft,['test'],[])))
  await act(async () => listeners[0](completion))
  expect(state.exports.running).toBe(true)
  expect(state.exports.event?.jobId).toBe('job2')
  await act(async () => listeners[1]({...completion,jobId:'job2'}))
  expect(state.exports.running).toBe(false)
})

it('flushes pending exclusions before transfer and applies returned metadata without stale saves', async () => {
  let state!: {workspace:ReturnType<typeof useWorkspace>; files:ReturnType<typeof useFileActions>}
  let failure: unknown
  function Probe() {
    const workspace = useWorkspace()
    const files = useFileActions(workspace,{onConflict:()=>{},onMutation:()=>{},onError:error=>{failure=error}})
    state = {workspace,files}; return null
  }
  dispose = (await mount(<Probe />)).unmount
  const saved = deferred<void>()
  vi.mocked(api.savePackage).mockReturnValueOnce(saved.promise)
  const conflicts = vi.spyOn(api,'transferConflicts').mockResolvedValue([])
  const updated = {...testPackage,excluded:['Props/file.duf']}
  vi.spyOn(api,'transferFiles').mockResolvedValue({package:updated,changes:[{from:'People/file.duf',to:'Props/file.duf',kind:'move'}],results:[{path:'People/file.duf',status:'partial',message:appError('destinationChanged')}],saveError:null})
  await act(async () => state.workspace.edit({...testPackage,excluded:['People/file.duf']}))
  let transfer!: Promise<void>
  await act(async () => { transfer = state.files.transfer('test','Props',['People/file.duf'],'move') })
  expect(conflicts).not.toHaveBeenCalled()
  await act(async () => { saved.resolve(); await transfer })
  expect(state.workspace.packages[0].excluded).toEqual(['Props/file.duf'])
  expect(state.files.mutation?.changes[0].to).toBe('Props/file.duf')
  expect(errorText(failure,translator('en'))).toContain('destination changed')
  expect(errorText(failure,translator('de'))).toContain('Ziel hat sich')
  await act(async () => state.workspace.flush())
  expect(api.savePackage).toHaveBeenCalledOnce()
})

it('uses the normalized settings returned by Rust', async () => {
  let workspace!: ReturnType<typeof useWorkspace>
  function Probe() { workspace = useWorkspace(); return null }
  dispose = (await mount(<Probe />)).unmount
  vi.spyOn(api,'saveSettings').mockResolvedValue({...testSettings,stores:[{name:'Custom',prefix:'RO'}],defaultStore:'Custom'})
  await act(async () => workspace.changeSettings({...testSettings,stores:[{name:' Custom ',prefix:' ro '}],defaultStore:' custom '}))
  expect(workspace.settings?.stores).toEqual([{name:'Custom',prefix:'RO'}])
  expect(workspace.settings?.defaultStore).toBe('Custom')
})

it('restores every new theme through workspace loading and retains the saved choice on write failure', async () => {
  let workspace!: ReturnType<typeof useWorkspace>
  function Probe() { workspace = useWorkspace(); return null }
  let saved = {...testSettings}
  vi.mocked(api.loadWorkspace).mockImplementation(async()=>({packages:[testPackage],settings:saved,warnings:[],worklist:{revision:0,entries:[{kind:'package',id:'test'}],canUndo:false,saveError:null,warnings:[]}}))
  vi.spyOn(api,'saveSettings').mockImplementation(async next => { saved = {...next}; return saved })
  dispose=(await mount(<Probe/>)).unmount
  for (const theme of themes.slice(2)) {
    await act(async()=>workspace.changeSettings({...saved,theme:theme.id}))
    expect(saved.theme).toBe(theme.id)
    dispose()
    dispose=(await mount(<Probe/>)).unmount
    expect(workspace.settings?.theme).toBe(theme.id)
    vi.mocked(api.saveSettings).mockRejectedValueOnce(appError('ioError'))
    await act(async()=>{ await expect(workspace.changeSettings({...saved,theme:'dark'})).rejects.toMatchObject({code:'ioError'}) })
    expect(workspace.settings?.theme).toBe(theme.id)
  }
})

it('refreshes targets after organization changes, rejects late previews and keeps saved settings on failure', async () => {
  let workspace!: ReturnType<typeof useWorkspace>
  function Probe() { workspace = useWorkspace(); return null }
  vi.mocked(api.previewOutputs).mockResolvedValue([{id:'test',directory:'C:/Output',key:'C:/OUTPUT'}])
  dispose = (await mount(<Probe />)).unmount
  expect(workspace.outputs.test.directory).toBe('C:/Output')
  const old = deferred<OutputDirectory[]>()
  vi.mocked(api.previewOutputs).mockReturnValueOnce(old.promise)
  let refresh!: Promise<void>
  await act(async () => { refresh = workspace.refreshOutputs(['test']) })
  vi.spyOn(api,'saveSettings').mockImplementation(async settings => settings)
  const dated = {id:'test',directory:'C:/Output/2026-01-09',key:'C:/OUTPUT/2026-01-09'}
  vi.mocked(api.previewOutputs).mockResolvedValue([dated])
  await act(async () => workspace.changeSettings({...testSettings,outputOrganization:'byDate'}))
  await act(async () => { old.resolve([{id:'test',directory:'C:/Old',key:'C:/OLD'}]); await refresh })
  expect(workspace.outputs.test).toEqual(dated)
  vi.mocked(api.saveSettings).mockRejectedValueOnce(appError('ioError'))
  await act(async () => {
    await expect(workspace.changeSettings({...testSettings,outputOrganization:'flat'})).rejects.toMatchObject({code:'ioError'})
  })
  expect(workspace.settings?.outputOrganization).toBe('byDate')
  expect(workspace.outputs.test).toEqual(dated)
  // A failed target refresh must not leave a stale destination on screen.
  vi.mocked(api.previewOutputs).mockRejectedValueOnce(appError('invalidOutputDirectory'))
  await act(async () => workspace.refreshOutputs(['test']))
  expect(workspace.outputs.test).toBeUndefined()
  expect(workspace.warnings).toContainEqual(appError('invalidOutputDirectory'))
  vi.mocked(api.previewOutputs).mockResolvedValue([dated])
  await act(async () => workspace.refreshOutputs(['test']))
  expect(workspace.warnings).toEqual([])
})

it('starts with the reviewed context and exact per-package paths after the local day changes', async () => {
  let exports!: ReturnType<typeof useExport>
  function Probe() { exports = useExport(useWorkspace()); return null }
  dispose = (await mount(<Probe />)).unmount
  const draft: ExportDraft = {
    context:{baseDir:'C:/Output',organization:'byDate',localDate:'2025-12-31'},
    rows:[{id:'test',name:'Test',filename:'test.zip',outputDir:'C:/Output/2025-12-31',outputPath:'C:/Output/2025-12-31/test.zip',issues:[],existing:false,stamp:null}],
  }
  vi.spyOn(api,'preflight').mockResolvedValue(draft)
  const start = vi.spyOn(api,'startExport').mockResolvedValue('job')
  let prepared!: ExportDraft | null
  await act(async () => { prepared = await exports.prepare(['test'],async () => null) })
  await act(async () => exports.start(prepared!,['test'],[]))
  expect(start).toHaveBeenCalledWith(draft.context,[{id:'test',outputPath:'C:/Output/2025-12-31/test.zip'}],[],expect.any(Function))
})

it('ignores a late cancellation failure from a completed export', async () => {
  let exports!: ReturnType<typeof useExport>
  function Probe() { exports = useExport(useWorkspace()); return null }
  dispose = (await mount(<Probe />)).unmount
  const listeners: ((event:JobEvent) => void)[] = []
  vi.spyOn(api,'startExport').mockImplementation(async (_ids,_output,_replace,listener) => { listeners.push(listener); return `job${listeners.length}` })
  const cancelled = deferred<void>()
  vi.spyOn(api,'cancelExport').mockReturnValue(cancelled.promise)
  const draft: ExportDraft = {rows:[],context:{baseDir:'C:/Output',organization:'flat',localDate:'2026-01-09'}}
  await act(async () => exports.start(draft,[],[]))
  await act(async () => exports.cancel())
  await act(async () => listeners[0]({jobId:'job1',packageId:null,phase:'finished',done:0,total:0,results:[]}))
  await act(async () => exports.start(draft,[],[]))
  await act(async () => cancelled.reject(appError('exportNotFound')))
  expect(exports.running).toBe(true)
  expect(exports.cancelError).toBeUndefined()
  expect(exports.cancelling).toBe(false)
})


it('keeps successful removals visible when structure saving fails and retries before closing', async () => {
  let workspace!: ReturnType<typeof useWorkspace>
  function Probe(){ workspace=useWorkspace();return null }
  const second={...testPackage,id:'second',name:'Second'}
  vi.mocked(api.loadWorkspace).mockResolvedValue({packages:[testPackage,second],settings:testSettings,warnings:[],worklist:{revision:1,entries:[{kind:'package',id:'test'},{kind:'package',id:'second'}],canUndo:true,saveError:null,warnings:[]}})
  dispose=(await mount(<Probe/>)).unmount
  const failure=appError('ioError')
  vi.spyOn(api,'removePackage').mockResolvedValueOnce({revision:2,entries:[{kind:'package',id:'second'}],canUndo:false,saveError:failure,warnings:[]})
    .mockResolvedValueOnce({revision:3,entries:[],canUndo:false,saveError:failure,warnings:[]})
  const retry=vi.spyOn(api,'retryWorklist').mockResolvedValue({revision:3,entries:[],canUndo:false,saveError:null,warnings:[]})
  await act(async()=>workspace.remove('test'))
  expect(workspace.packages.map(p=>p.id)).toEqual(['second']);expect(workspace.saveError).toEqual(failure)
  await act(async()=>workspace.remove('second'))
  expect(workspace.packages).toEqual([]);expect(retry).not.toHaveBeenCalled()
  await act(async()=>workspace.flushForClose())
  expect(retry).toHaveBeenCalledOnce();expect(workspace.saveError).toBeNull()
})

it('saves pending metadata before structural actions and never adopts a late revision', async () => {
  let workspace!: ReturnType<typeof useWorkspace>
  function Probe(){ workspace=useWorkspace();return null }
  dispose=(await mount(<Probe/>)).unmount
  const request=deferred<import('./api').WorklistState>()
  const change=vi.spyOn(api,'changeWorklist').mockReturnValueOnce(request.promise).mockResolvedValueOnce({revision:3,entries:[{kind:'package',id:'test'}],canUndo:true,saveError:null,warnings:[]})
  await act(async()=>workspace.edit({...testPackage,name:'Unsaved edit'}))
  let earlier!: Promise<boolean>
  await act(async()=>{earlier=workspace.changeWorklist({kind:'detach',ids:['test']})})
  expect(api.savePackage).toHaveBeenCalledWith({...testPackage,name:'Unsaved edit'})
  expect(change).toHaveBeenCalledWith(0,{kind:'detach',ids:['test']})
  await act(async()=>workspace.changeWorklist({kind:'detach',ids:['test']}))
  await act(async()=>{request.resolve({revision:2,entries:[],canUndo:false,saveError:null,warnings:[]});await earlier})
  expect(workspace.worklist?.revision).toBe(3);expect(workspace.packages[0].name).toBe('Unsaved edit')
})
