import { resolveTheme } from './themes'
import { ThemeMenu } from './ThemePicker'
import { AlwaysOnTopButton } from './AlwaysOnTopButton'
import { ClosingContext } from './closeRequest'
import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { api, type ExportApproval, type ExportPreflight, type OutputOrganization, type FileEntry, type Package, type Update } from './api'
import { DetailsDialog, ExportProgress, ExportReview, Modal, NameDialog, SettingsDialog, ValidationDialog, type SettingsTab } from './dialogs'
import { Icon } from './Icon'
import { resolveLanguage, errorText, translator } from './messages'
import { Inspector } from './panels'
import { Worklist } from './Worklist'
import { FilePanel } from './FilePanel'
import { useExport } from './useExport'
import { useFileActions, type FileConflict } from './useFileActions'
import { useAppActions } from './useAppActions'
import { useWorkspace } from './useWorkspace'
import { remapFilePath } from './fileSelection'
import { displayPart, displaySku, excludedBy, filterPackages, markVisible, nextPackageAfterRemoval, removePackages, workspacePreviews } from './workflow'
import { PanelDivider } from './PanelDivider'
import { SingleSelect } from './SingleSelect'
import { outputOrganizationOptions } from './outputOrganizations'
import { Logo } from './Logo'
import { GroupSyncDialog } from './GroupSyncDialog'
import type { SyncSession } from './api'
import { useArchiveImport } from './useArchiveImport'
import { ArchiveImportDialog } from './ArchiveImportDialog'
import { useNativeDrop } from './useNativeDrop'

type Dialog =
  | {kind:'import';preferred?:string}
  | {kind: 'sync'; session: SyncSession; source: string}
  | { kind: 'package' | 'guid' }
  | { kind: 'folder'; id: string; folder: string }
  | { kind: 'validation'; id: string }
  | { kind: 'remove'; items: Package[]; hidden: string[]; failures?: {id: string; error: unknown}[] }
  | { kind: 'settings'; tab: SettingsTab; themePreview?: string }
  | { kind: 'details'; entry: FileEntry }
  | ({ kind: 'copy' } & FileConflict)
  | { kind: 'trash'; id: string; paths: string[] }
  | ({ kind: 'review' | 'export' } & ExportPreflight)

export function App() {
  const workspace = useWorkspace()
  const { settings, packages } = workspace
  const previews = workspacePreviews(workspace.previews, workspace.outputs)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [query, setQuery] = useState('')
  const [panel, setPanel] = useState<'list' | 'properties' | 'files'>('list')
  const [tab, setTab] = useState<'files' | 'preview'>('files')
  const [folder, setFolder] = useState('')
  const [navigationRequest, setNavigationRequest] = useState(0)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [touched, setTouched] = useState<Record<string, Set<string>>>({})
  const exports = useExport(workspace)
  const imports = useArchiveImport(workspace, () => setNavigationRequest(current=>current+1))
  const actions = useAppActions(workspace, () => exports.isRunning() || imports.isRunning())
  const { busy, closing, error, setError, closeError, setCloseError, perform, run } = actions
  const files = useFileActions(workspace, {
    onConflict: conflict => setDialog({kind:'copy', ...conflict}),
    onMutation: result => setFolder(current => remapFilePath(current, result.changes)),
    onError: setError,
  })
  const { revision, mutation: fileMutation, refresh, copy } = files
  const { event, cancelling, cancelError } = exports
  const [update, setUpdate] = useState<Update | null>(null)
  const [updateState, setUpdateState] = useState<'checking' | 'current' | 'available' | 'idle'>('idle')
  const [updateError, setUpdateError] = useState<unknown>()
  const updateStarted = useRef(false)
  const listFocus = useRef<string | null | undefined>(undefined)
  const language = settings?.language ?? 'de'
  const t = translator(language)
  const item = packages.find(entry => entry.id === activeId) ?? packages[0]
  const id = item?.id
  useEffect(() => {
    if (activeId === null && packages[0]) setActiveId(packages[0].id)
  }, [activeId, packages])
  const [cover, setCover] = useState<{ id: string; name: string; url: string | null }>()
  const visible = filterPackages(packages, query)
  const close = () => { setDialog(null); setCloseError(undefined) }
  const exporting = exports.running
  const locked = busy || closing || exporting || imports.running
  const activate = (next: string) => {
    if (next !== id) {
      setFolder('')
      files.reset()
    }
    setActiveId(next)
    setPanel('properties')
  }
  const activeGroup = workspace.worklist?.entries.find(entry => entry.kind === 'group' && id && entry.members.includes(id))
  const openSync = (group: string) => void run(async () => {
    await workspace.prepareMetadataAction()
    const loaded = await api.loadGroupSync(group)
    if (loaded.status === 'unavailable') {
      workspace.adoptWorklist(loaded.worklist, true)
      setDialog(null)
      throw loaded.error
    }
    const session = loaded.session
    workspace.adoptSyncSession(session)
    setDialog({kind:'sync',session,source:session.data.packages.find(p=>p.id===id)?.id ?? session.data.packages[0].id})
  })
  const beginImport = async(paths:string[],preferred?:string)=>{
    await workspace.prepareMetadataAction()
    setDialog({kind:'import',preferred})
    await imports.analyze(paths)
  }
  const openImport = (preferred?:string) => void run(async()=>{
    const paths=await api.chooseArchives(t('archiveTitle'))
    if(!paths?.length)return
    await beginImport(paths,preferred)
  })
  const nativeDrop=useNativeDrop({blocked:locked||!!dialog||!settings,packageId:id,folder,
    onImport:(paths,preferred)=>void run(()=>beginImport(paths,preferred)),
    onCopy:(paths,destination)=>{if(id)void run(()=>copy(id,destination,paths))},
    onCover:path=>{if(id)void run(()=>workspace.setCover(id,path))},onError:setError,
  })
  const confirmRemoval = (ids: string[]) => {
    const items = packages.filter(item => ids.includes(item.id))
    if (!items.length) return
    setError(undefined)
    setDialog({kind:'remove',items,hidden:items.filter(item => !visible.some(row => row.id === item.id)).map(item => item.id)})
  }
  useEffect(() => {
    if (dialog || locked || listFocus.current === undefined) return
    const target = listFocus.current
    listFocus.current = undefined
    const row = target && document.querySelector<HTMLElement>(`.package-row[data-package-id="${target}"] .package-link`)
    const focusTarget = row || document.querySelector<HTMLElement>('.package-list')
    focusTarget?.focus()
  }, [dialog, locked])
  const reveal = (itemId: string, field: string) => setTouched(current => ({
    ...current, [itemId]: new Set([...(current[itemId] ?? []), field]),
  }))
  const change = (changes: Partial<Package>) => {
    if (item && actions.available()) workspace.edit({ ...item, ...changes })
  }

  const effectiveTheme = dialog?.kind === 'settings' ? dialog.themePreview ?? settings?.theme : settings?.theme
  useEffect(() => {
    document.documentElement.lang = resolveLanguage(language).locale
    const theme = resolveTheme(effectiveTheme ?? 'dark')
    document.documentElement.dataset.theme = theme.id
    document.documentElement.dataset.themeCategory = theme.category
    document.documentElement.style.colorScheme = theme.colorScheme
  }, [language, effectiveTheme])

  useEffect(() => {
    if (!id || !item?.cover) return
    let live = true
    const name = item.cover
    void api.getCover(id).then(url => { if (live) setCover({ id, name, url }) })
      .catch(failure => { if (live) setError(failure) })
    return () => { live = false }
  }, [id, item?.cover])

  const checkUpdates = async () => {
    setUpdateState('checking')
    setUpdateError(undefined)
    try {
      const available = await api.checkUpdates()
      setUpdate(available)
      setUpdateState(available ? 'available' : 'current')
    } catch (failure) {
      setUpdateError(failure)
      setUpdateState('idle')
    }
  }
  const checkAtStartup = useEffectEvent(() => void checkUpdates())
  useEffect(() => {
    if (settings && !updateStarted.current) {
      updateStarted.current = true
      if (settings.checkUpdates) checkAtStartup()
    }
  }, [settings])

  const reloadFromExplorer = useEffectEvent(() => {
    if (!locked && !dialog) {
      if (id) refresh(id)
      void workspace.refreshOutputs(packages.filter(item => item.id !== id).map(item => item.id))
    }
  })
  useEffect(() => {
    const onFocus = () => reloadFromExplorer()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  const chooseSources = (directory: boolean, targetFolder: string) => id && void run(async () => {
    const sources = await api.chooseSources(t(directory ? 'addFolders' : 'addFiles'), directory)
    if (sources?.length) await copy(id, targetFolder, sources)
  })
  const chooseCover = () => id && void run(async () => {
    const source = await api.chooseCover(t('chooseCover'))
    if (source) await workspace.setCover(id, source)
  })
  const chooseOutput = async () => {
    if (!settings) return null
    const output = await api.chooseOutput(t('chooseOutput'))
    if (output) await workspace.changeSettings({ ...settings, outputDir: output })
    return output
  }
  const review = (ids: string[]) => void run(async () => {
    const draft = await exports.prepare(ids, chooseOutput)
    if (!draft) return
    draft.rows.forEach(row => reveal(row.id, '*'))
    setDialog({kind:'review', ...draft})
  })
  const validate = (itemId: string) => {
    reveal(itemId, '*')
    setDialog({ kind: 'validation', id: itemId })
    void workspace.flush().then(() => workspace.refresh(itemId)).catch(setError)
  }
  const jump = (itemId: string, field: string) => {
    activate(itemId)
    if (field === 'output') { setPanel('files'); setTab('preview') }
    if (field === 'content' || field === 'support') { setPanel('files'); setTab('files') }
    close()
    const focusTarget = () => window.requestAnimationFrame(() => {
      const target = document.getElementById(`field-${field === 'support' ? 'content' : field}`)
      target?.scrollIntoView({ block: 'nearest' })
      if (target?.matches(':disabled')) target.closest('.field')?.querySelector<HTMLElement>('input:not(:disabled)')?.focus()
      else target?.focus()
    })
    if (field === 'support') void run(async () => {
      const root = await api.listFiles(itemId, '')
      const runtime = root.find(entry => entry.directory && entry.name.toLowerCase() === 'runtime')
      const entries = runtime ? await api.listFiles(itemId, runtime.path) : []
      const support = entries.find(entry => entry.directory && entry.name.toLowerCase() === 'support')
      setFolder(support?.path ?? runtime?.path ?? '')
      setNavigationRequest(current => current + 1)
    }).then(focusTarget)
    else focusTarget()
  }
  const startExport = (draft: Extract<Dialog, { kind: 'review' | 'export' }>, ids: string[], replace: ExportApproval[]) => void run(async () => {
    setDialog({ ...draft, kind: 'export' })
    try { await exports.start(draft, ids, replace) }
    catch (error) { close(); throw error }
  })

  const renderDialog = () => {
    if (!dialog || !settings) return null
    switch (dialog.kind) {
      case 'import': return <ArchiveImportDialog imports={imports} packages={packages} preferred={dialog.preferred} t={t} language={language}
        onClose={()=>void run(async()=>{await imports.close();close()})}
        onFinish={()=>void run(async()=>{await imports.close();setPanel('list');listFocus.current=null;close()})}
        onStart={(preview,policy)=>perform(async()=>{await workspace.prepareMetadataAction();await imports.start(preview,policy)})}/>
      case 'sync': return <GroupSyncDialog key={JSON.stringify(dialog.session)} session={dialog.session} source={dialog.source} t={t} onClose={close} onReload={() => openSync(dialog.session.data.group)} onApply={(request, preview) => perform(async () => {
        await workspace.prepareMetadataAction()
        const result = await api.applyGroupSync(request,dialog.session.context,preview.stamp)
        workspace.adoptSync(result,preview)
        return result
      })} />
      case 'package': return <NameDialog type="package" t={t} onClose={close} onConfirm={name => perform(async () => {
        const next = await workspace.add(name)
        setQuery('')
        activate(next.id)
        close()
      })} />
      case 'folder': return <NameDialog type="folder" folder={dialog.folder} t={t} onClose={close} onConfirm={name => perform(async () => {
        await files.createFolder(dialog.id, dialog.folder, name)
        close()
      })} />
      case 'guid': return <Modal title={t('generateGuid')} t={t} onClose={close} footer={<>
        <button type="button" onClick={close}>{t('cancel')}</button>
        <button className="primary" type="button" onClick={() => { change({ guid: crypto.randomUUID() }); close() }}>{t('generateGuid')}</button>
      </>}><p>{t('guidConfirm')}</p></Modal>
      case 'remove': return <Modal title={t('removePackages', {count:dialog.items.length})} t={t} onClose={busy ? undefined : close} footer={<>
        <button type="button" onClick={close} disabled={busy}>{t('cancel')}</button>
        <button type="button" className="danger" disabled={busy} onClick={() => void run(async () => {
          if (!dialog.failures) await workspace.flush()
          const order = packages.map(item => item.id)
          const removed = new Set<string>()
          let current: string | null = id ?? null
          const failures = await removePackages(dialog.failures?.map(row => row.id) ?? dialog.items.map(item => item.id), async target => {
            await workspace.remove(target)
            removed.add(target)
            setSelected(selection => markVisible(selection, [{id:target}], false))
            const next = nextPackageAfterRemoval(order, current, removed)
            if (next !== current) {
              current = next
              setActiveId(next)
              setFolder('')
              files.reset()
            }
          })
          if (removed.size) listFocus.current = current
          if (failures.length) setDialog({...dialog, failures})
          else close()
        })}>{t(dialog.failures ? 'retryRemoval' : 'remove')}</button>
      </>}><p>{t('removePackagesConfirm', {count:dialog.items.length})}</p>
        {!!dialog.hidden.length && <p className="muted">{t('hiddenTargets', {count:dialog.hidden.length})}</p>}
        <ul className="removal-list">{dialog.items.map(item => <li key={item.id}>
          <strong>{item.name || t('unnamed')}</strong><small>{displaySku(item.sku, t)} · Part {displayPart(item.part)}</small>
        </li>)}</ul>
        {dialog.failures && <div role="status"><p>{t('removalProgress',{done:dialog.items.length-dialog.failures.length,total:dialog.items.length})}</p>
          <ul>{dialog.failures.map(row => <li key={row.id} className="error">{dialog.items.find(item => item.id === row.id)?.name}: {errorText(row.error,t)}</li>)}</ul>
        </div>}
        {error != null && <p className="error" role="alert">{errorText(error, t)}</p>}
      </Modal>
      case 'trash': return <Modal title={t('deleteFiles')} t={t} onClose={busy ? undefined : close} footer={<>
        <button type="button" onClick={close} disabled={busy}>{t('cancel')}</button>
        <button type="button" className="danger" disabled={busy} onClick={() => void run(async () => {
          await files.trash(dialog.id, dialog.paths)
          close()
        })}>{t('deleteFiles')}</button>
      </>}><p>{t('deleteFilesConfirm',{count:dialog.paths.length})}</p><ul>{dialog.paths.map(path => <li key={path} className="path">{path}</li>)}</ul>
        {error != null && <p className="error" role="alert">{errorText(error,t)}</p>}
      </Modal>
      case 'copy': {
        const confirm = (replace: boolean) => void run(async () => {
          await files.resolveConflict(dialog, replace)
          close()
        })
        return <Modal title={t('conflicts')} t={t} onClose={busy ? undefined : close} footer={<>
          <button type="button" onClick={close} disabled={busy}>{t('cancel')}</button>
          <button type="button" onClick={() => confirm(false)} disabled={busy}>{t('skip')}</button>
          <button className="primary" type="button" onClick={() => confirm(true)} disabled={busy}>{t('replace')}</button>
        </>}>
          <p>{t('copyConflicts')}</p>
          <ul>{dialog.conflicts.map(({ path }) => <li key={path} className="path">{path}</li>)}</ul>
          {error != null && <p className="error" role="alert">{errorText(error, t)}</p>}
        </Modal>
      }
      case 'validation': return <ValidationDialog t={t} onClose={close}
        item={packages.find(entry => entry.id === dialog.id)!}
        preview={previews[dialog.id]} onJump={field => jump(dialog.id, field)} />
      case 'details': return <DetailsDialog entry={dialog.entry} t={t} language={language} onClose={close} />
      case 'review': return <ExportReview rows={dialog.rows} t={t} onClose={close}
        onStart={(ids, replace) => startExport(dialog, ids, replace)} />
      case 'export': return <ExportProgress rows={dialog.rows} event={event} results={event?.results ?? null}
        cancelling={cancelling} error={cancelError} t={t} onClose={close} onCancel={exports.cancel} />
      case 'settings': return <SettingsDialog initialTab={dialog.tab} settings={settings} t={t}
        onPreviewTheme={themePreview => setDialog(current => current?.kind === 'settings' ? {...current,themePreview} : current)}
        update={update} updateStatus={updateError != null ? errorText(updateError, t) : updateState === 'checking' ? t('updateChecking') :
          updateState === 'current' ? t('updateCurrent') : updateState === 'available' && update ? t('updateAvailable', { version: update.version }) : ''}
        onCheck={() => void checkUpdates()} onClose={close} onSave={async next => {
          await perform(() => workspace.changeSettings(next))
          close()
        }} />
    }
  }

  return <ClosingContext value={{closing,error:closeError}}><div className="app">
    <header className="app-header">
      <div className="brand"><Logo /><strong>DIM Creator</strong><span className="version">v3</span></div>
      {update && <button type="button" className="text update-link" disabled={locked} onClick={() => setDialog({ kind: 'settings', tab: 'updatesAbout' })}>{t('updateAvailable', { version: update.version })}</button>}
      <div className="header-actions">
        <ThemeMenu value={settings?.theme ?? 'dark'} disabled={!settings || locked || !!dialog} t={t}
          onChange={theme => run(async () => { if (settings) await workspace.changeSettings({...settings,theme}) })}/>
        <AlwaysOnTopButton t={t} closing={closing} onError={setError}/>
        <button type="button" className="icon-btn" aria-label={t('settings')} title={t('settings')} disabled={!settings || locked} onClick={() => setDialog({ kind: 'settings', tab: 'general' })}><Icon name="settings" /></button>
      </div>
    </header>
    {!settings ? <main className="startup">
      {workspace.loadError != null ? <>
        <p role="alert">{errorText(workspace.loadError, t)}</p>
        <button type="button" onClick={() => void workspace.load()}>{t('retry')}</button>
      </> : <p role="status">{t('loading')}</p>}
    </main> : <>
      <nav className="mobile-tabs" aria-label={t('panelNavigation')}>
        {(['list', 'properties', 'files'] as const).map(key => <button key={key} type="button" aria-current={panel === key ? 'page' : undefined}
          disabled={locked} onClick={() => setPanel(key)}>{t(key)}</button>)}
      </nav>
      <main className="main-grid" data-panel={panel} inert={locked} aria-busy={locked}>
        <Worklist nativeDrop={nativeDrop.preview?.area==='worklist'?(nativeDrop.preview.valid?'valid':'invalid'):undefined} packages={packages} visible={visible} active={id ?? null} previews={previews} selected={selected} query={query}
          worklist={workspace.worklist!} onImport={()=>openImport()} onSynchronize={openSync} onStructure={action => run(() => workspace.changeWorklist(action))}
          t={t} language={language} disabled={locked} onQuery={setQuery} onActive={activate}
          onCheck={(packageId, checked) => setSelected(current => markVisible(current, [{ id: packageId }], checked))}
          onCheckAll={checked => setSelected(current => markVisible(current, visible, checked))}
          onSelection={setSelected} onBuild={review} onRemove={confirmRemoval}
          onClear={() => setSelected(new Set())} onAdd={() => setDialog({ kind: 'package' })} onValidate={validate} />
        <Inspector synchronize={activeGroup ? () => openSync(activeGroup.id) : undefined} item={item} settings={settings} preview={id ? previews[id] : undefined} disabled={locked} t={t}
          cover={cover?.id === id && cover?.name === item?.cover ? cover?.url ?? null : null}
          shown={id ? touched[id] ?? new Set() : new Set()} reveal={field => { if (id) reveal(id, field) }} change={change}
          chooseCover={chooseCover} removeCover={() => { if (id) void run(() => workspace.setCover(id, null)) }}
          generateGuid={() => setDialog({ kind: 'guid' })} manageTags={() => setDialog({ kind: 'settings', tab: 'tags' })}
          manageStores={() => setDialog({ kind: 'settings', tab: 'stores' })}
          build={() => { if (id) review([id]) }} remove={() => { if (id) confirmRemoval([id]) }} />
        <PanelDivider width={settings.inspectorWidth} disabled={locked} t={t}
          onChange={width => run(() => workspace.changeSettings({...settings, inspectorWidth:width}))} />
        <FilePanel archiveDrop={nativeDrop.archives?{active:nativeDrop.preview?.area==='archive',valid:nativeDrop.preview?.area!=='archive'||nativeDrop.preview.valid}:undefined} key={id} item={item} t={t} language={language} preview={id ? previews[id] : undefined} tab={tab} folder={folder}
          mutation={fileMutation} onTransfer={(paths, target, mode) => id && void run(() => files.transfer(id, target, paths, mode))} onRename={(path, name) => perform(() => files.rename(id!, path, name))} onDelete={paths => id && setDialog({kind:"trash",id,paths})}
          revision={revision} navigationRequest={navigationRequest} output={id ? workspace.outputs[id]?.directory ?? null : null} disabled={locked}
          onTab={next => {setTab(next);if(next==='preview' && id && previews[id]?.stale) refresh(id)}} onFolder={setFolder} onDetail={entry => setDialog({ kind: 'details', entry })}
          onExclude={path => { if (item) change({ excluded: excludedBy(item, path) === 'self'
            ? item.excluded.filter(entry => entry.toLowerCase() !== path.toLowerCase()) : [...item.excluded, path] }) }}
          onAdd={chooseSources} onImport={()=>openImport(id)} onCreateFolder={targetFolder => { if (id) setDialog({ kind: 'folder', id, folder: targetFolder }) }}
          onOpenFolder={targetFolder => { if (id) void run(() => api.openFolder(id, targetFolder)) }}
          onRefresh={() => { if (id) refresh(id) }} onValidation={() => { if (id) validate(id) }}
          onNewPackage={() => setDialog({ kind: 'package' })} />
      </main>
      <footer className="output-bar">
        <div className="output-controls"><div className="output-picker"><label htmlFor="output-path">{t('output')}</label>
          <input id="output-path" readOnly value={settings.outputDir ?? ''} placeholder={t('unsetOutput')} title={settings.outputDir ?? undefined} />
          <button type="button" className="icon-btn" disabled={locked} title={t('chooseOutput')}
            aria-label={t('chooseOutput')} onClick={() => void run(async () => { await chooseOutput() })}><Icon name="folderopen" /></button>
        </div>
          <div className="output-organization"><SingleSelect id="output-organization" label={t('outputOrganization')}
            value={settings.outputOrganization} options={outputOrganizationOptions(t)} disabled={locked}
            onChange={value => void run(() => workspace.changeSettings({...settings,outputOrganization:value as OutputOrganization}))} /></div>
        </div>
        <div className="export-actions">
          <button type="button" disabled={locked || !packages.length} onClick={() => review(packages.map(entry => entry.id))}>{t('buildAll', { count: packages.length })}</button>
          <button type="button" className="primary" disabled={locked || !selected.size}
            onClick={() => review(packages.filter(entry => selected.has(entry.id)).map(entry => entry.id))}><Icon name="play" />{t('buildSelected', { count: selected.size })}</button>
        </div>
      </footer>
      <div className="status-bar" role="status">
        <span className={workspace.saveError != null ? 'error' : closing || workspace.saving || busy || !settings ? 'muted' : 'success'} aria-hidden="true">{workspace.saveError != null ? '!' : '●'}</span>
        {workspace.saveError != null ? <>{t('saveFailed')} <button type="button" className="text small" disabled={locked} onClick={() => void run(() => workspace.flush())}>{t('retrySave')}</button></> :
          closing ? t('closing') : workspace.saving ? t('saving') : busy ? t('processing') : t('saved')}
      </div>
    </>}
    {nativeDrop.preview&&!locked&&!dialog&&<div className="file-drag-label native-drop-label" role="status" data-valid={nativeDrop.preview.valid} style={{left:Math.min(nativeDrop.preview.x+14,Math.max(8,window.innerWidth-300)),top:Math.min(nativeDrop.preview.y+14,window.innerHeight-90)}}>
      {nativeDrop.preview.valid?t(nativeDrop.preview.area==='worklist'?'archiveDropNew':nativeDrop.preview.area==='archive'?'archiveDropPackage':nativeDrop.preview.area==='cover'?'nativeDropCover':'nativeDropCopy',{count:nativeDrop.preview.count,name:item?.name||t('unnamed'),path:`Content${nativeDrop.preview.folder?'/'+nativeDrop.preview.folder:''}`}):t(nativeDrop.preview.area==='cover'?'unsupportedCoverFormat':'archiveDropOnly')}
    </div>}
    {workspace.warnings.length > 0 && <div className="workspace-warnings" role="status">{[...new Set(workspace.warnings.map(warning => errorText(warning, t)))].join(' · ')}</div>}
    {error != null && <div className="notification error" role="alert">{errorText(error, t)} <button className="text" type="button" aria-label={t('close')} onClick={() => setError(undefined)}>×</button></div>}
    {!dialog && closeError != null && <div className="notification error" role="alert">{errorText(closeError,t)} <button type="button" className="text" aria-label={t('close')} onClick={() => setCloseError(undefined)}>×</button></div>}
    {renderDialog()}
  </div></ClosingContext>
}
