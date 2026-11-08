import { useEffect, useEffectEvent, useState, useRef, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react'
import { api, type FileEntry, type Package, type Preview, type FileMutation, type TransferMode } from './api'
import { containsPath, remapFilePath, selectFiles } from './fileSelection'
import { useValidation } from './useValidation'
import { Icon } from './Icon'
import { Logo } from './Logo'
import { Popup, type PopupAnchor } from './Popup'
import { StatusButton } from './StatusButton'
import { errorText, sizeText, type Message, type Translate } from './messages'
import { displayPart, displaySku, excludedBy } from './workflow'

type FilesProps = {
  archiveDrop?: {active:boolean;valid:boolean}
  item?: Package
  t: Translate
  language: string
  preview?: Preview
  tab: 'files' | 'preview'
  folder: string
  mutation?: FileMutation
  onTransfer: (paths: string[], folder: string, mode: TransferMode) => void
  onRename: (path: string, name: string) => Promise<void>
  onDelete: (paths: string[]) => void
  revision: number
  navigationRequest: number
  output: string | null
  disabled: boolean
  onTab: (tab: 'files' | 'preview') => void
  onFolder: (path: string) => void
  onExclude: (path: string) => void
  onDetail: (entry: FileEntry) => void
  onAdd: (directory: boolean, folder: string) => void
  onCreateFolder: (folder: string) => void
  onOpenFolder: (folder: string) => void
  onRefresh: () => void
  onValidation: () => void
  onNewPackage: () => void
  onImport?: () => void
}

type FileMenu = { anchor: PopupAnchor; kind: 'add' | 'context'; folder: string; entry?: FileEntry }

export async function readDirectoryTree(read: (path: string) => Promise<FileEntry[]>, folder: string, expanded: ReadonlySet<string>) {
  const folders = new Map<string, FileEntry[]>()
  const errors = new Map<string, unknown>()
  const visit = async (path: string, visible: boolean) => {
    try {
      const entries = await read(path)
      folders.set(path, entries)
      await Promise.all(entries.filter(entry => entry.directory).map(async entry => {
        const selectedPath = entry.path === folder || folder.startsWith(entry.path + '/')
        const childVisible = visible && expanded.has(path)
        if (selectedPath || childVisible && expanded.has(entry.path)) await visit(entry.path, childVisible)
      }))
    } catch (error) { errors.set(path, error) }
  }
  await visit('', true)
  // Only listings actually read can prove a formerly expanded path was removed.
  const retained = new Set([...expanded].filter(path => {
    const parts = path.split('/').filter(Boolean)
    return parts.every((_, index) => {
      const parent = parts.slice(0, index).join('/')
      return !folders.has(parent) || folders.get(parent)!.some(entry => entry.directory && entry.path === parts.slice(0, index + 1).join('/'))
    })
  }))
  return { folders, errors, expanded: retained }
}

export function FilePanel(props: FilesProps) {
  const { t, item, preview, folder } = props
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [anchor, setAnchor] = useState<string | null>(null)
  const [clipboard, setClipboard] = useState<{id:string; paths:string[]; mode:TransferMode} | null>(null)
  const [renaming, setRenaming] = useState<{path:string; name:string; error?:unknown} | null>(null)
  const [renameBusy, setRenameBusy] = useState(false)
  const nameValidation = useValidation(renaming?.name ?? '', api.validateEntryName, !!renaming)
  const pointer = useRef<{id:number; x:number; y:number; paths:string[]; active:boolean} | null>(null)
  const suppressClick = useRef(false)
  const renameInput = useRef<HTMLInputElement>(null)
  const restoreFileFocus = useRef(false)
  const [drag, setDrag] = useState<{x:number; y:number; target:string | null} | null>(null)
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set(['']))
  const [folders, setFolders] = useState(new Map<string, FileEntry[]>())
  const [errors, setErrors] = useState(new Map<string, unknown>())
  const [loading, setLoading] = useState(false)
  const [menu, setMenu] = useState<FileMenu | null>(null)
  const entries = folders.get(folder) ?? []
  const selectedEntry = selected.size === 1 ? entries.find(entry => selected.has(entry.path)) : undefined
  const crumbs = folder.split('/').filter(Boolean)
  const missingFolder = useEffectEvent((path: string) => props.onFolder(path))
  useEffect(() => { if (!renameBusy && renaming?.error) renameInput.current?.focus() }, [renameBusy, renaming?.error])
  useEffect(() => { if (props.tab !== 'files') setRenaming(null) }, [props.tab])
  useEffect(() => { if (props.disabled) setMenu(null) }, [props.disabled])
  useEffect(() => {
    setSelected(new Set())
    setAnchor(null)
    setRenaming(current => current && current.path.split('/').slice(0,-1).join('/') === folder ? current : null)
    if (!folder) return
    const parts = folder.split('/').filter(Boolean)
    const ancestors = ['', ...parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'))]
    setExpanded(current => ancestors.every(path => current.has(path)) ? current : new Set([...current, ...ancestors]))
  }, [folder, props.navigationRequest])

  useEffect(() => {
    if (!item) return
    let live = true
    setLoading(true)
    void readDirectoryTree(path => api.listFiles(item.id, path), folder, expanded).then(result => {
      if (!live) return
      setFolders(result.folders)
      setSelected(current => new Set([...current].filter(path => result.folders.get(folder)?.some(entry => entry.path === path))))
      setErrors(result.errors)
      if (result.expanded.size !== expanded.size) setExpanded(result.expanded)
      setLoading(false)
      if (folder && !result.folders.has(folder)) missingFolder([...result.folders.keys()].filter(path => !path || containsPath(path,folder)).sort((a,b) => b.length-a.length)[0] ?? '')
    })
    return () => { live = false }
  }, [item?.id, folder, expanded, props.revision, props.navigationRequest])

  useEffect(() => {
    const result = props.mutation
    if (!result || result.package.id !== item?.id) return
    restoreFileFocus.current = true
    setExpanded(current => new Set([...current].map(path => remapFilePath(path,result.changes))))
    setSelected(current => new Set([...current].filter(path => !result.changes.some(change => change.kind === 'delete' && containsPath(change.from,path))).map(path => remapFilePath(path,result.changes)).filter(path => path.split('/').slice(0,-1).join('/') === folder)))
    setClipboard(current => {
      if (!current) return current
      const paths = current.paths.filter(path => !result.changes.some(change => change.kind !== 'copy' && containsPath(change.from,path)))
      return paths.length ? {...current,paths} : null
    })
  }, [props.mutation, item?.id])

  useEffect(() => {
    if (!props.disabled && restoreFileFocus.current) {
      restoreFileFocus.current = false
      document.getElementById('field-content')?.focus()
    }
  }, [props.mutation, props.disabled])

  const select = (path: string, ctrl = false, shift = false) => {
    const next = selectFiles(selected, anchor, path, entries.map(entry => entry.path), ctrl, shift)
    setSelected(next.paths)
    setAnchor(next.anchor)
  }
  const storeClipboard = (paths: string[], mode: TransferMode) => {
    if (item && paths.length) setClipboard({id:item.id,paths,mode})
  }
  const paste = (target: string) => {
    if (clipboard && clipboard.id === item?.id) props.onTransfer(clipboard.paths,target,clipboard.mode)
  }
  const startRename = (entry: FileEntry) => {
    const parent = entry.path.split('/').slice(0,-1).join('/')
    if (parent !== folder) props.onFolder(parent)
    setRenaming({path:entry.path,name:entry.name})
  }
  const finishRename = async () => {
    if (!renaming || renameBusy) return
    setRenameBusy(true)
    try { const failure = await nameValidation.check(); if (failure) { setRenaming({...renaming,error:failure}); return }; await props.onRename(renaming.path,renaming.name); setRenaming(null) }
    catch (error) { setRenaming({...renaming,error}) }
    finally { setRenameBusy(false) }
  }
  const pointerStart = (event: PointerEvent<HTMLElement>, entry: FileEntry) => {
    if (props.disabled || renaming || event.button !== 0 || (event.target as HTMLElement).closest('button,input')) return
    pointer.current = {id:event.pointerId,x:event.clientX,y:event.clientY,paths:selected.has(entry.path) ? [...selected] : [entry.path],active:false}
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const dragTarget = (x: number, y: number, paths: string[]) => {
    const target = document.elementFromPoint(x,y)?.closest<HTMLElement>('[data-drop-folder]')?.dataset.dropFolder
    return target === undefined || paths.some(path => containsPath(path,target)) ? null : target
  }
  const pointerMove = (event: PointerEvent) => {
    const current = pointer.current
    if (!current || current.id !== event.pointerId) return
    if (!(event.buttons & 1)) { pointer.current = null; setDrag(null); return }
    if (!current.active && Math.hypot(event.clientX-current.x,event.clientY-current.y) < 5) return
    current.active = true
    setSelected(new Set(current.paths))
    setMenu(null)
    setDrag({x:event.clientX,y:event.clientY,target:dragTarget(event.clientX,event.clientY,current.paths)})
  }
  const pointerEnd = (event: PointerEvent) => {
    const current = pointer.current
    pointer.current = null
    setDrag(null)
    if (!current?.active) return
    suppressClick.current = true
    window.setTimeout(() => { suppressClick.current = false }, 0)
    const target = dragTarget(event.clientX,event.clientY,current.paths)
    if (target !== null) props.onTransfer(current.paths,target,'move')
  }
  const navigate = (path: string) => {
    if (document.activeElement?.closest('.file-table')) document.getElementById('field-content')?.focus()
    setSelected(new Set())
    setAnchor(null)
    setMenu(null)
    props.onFolder(path)
  }
  const toggle = (path: string) => setExpanded(current => {
    const next = new Set(current)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    return next
  })
  const context = (event: MouseEvent<HTMLElement>, targetFolder: string, entry?: FileEntry) => {
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.focus()
    if (!entry) setSelected(new Set())
    else if (!selected.has(entry.path)) { setSelected(new Set([entry.path])); setAnchor(entry.path) }
    setMenu({ kind: 'context', folder: targetFolder, entry,
      anchor: { x: event.clientX, y: event.clientY, returnFocus: event.currentTarget } })
  }
  const shortcut = (event: KeyboardEvent) => {
    if (props.tab !== 'files' || props.disabled || (event.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"]')) return
    const tree = (event.target as HTMLElement).closest<HTMLElement>('[role="treeitem"]')
    const treePath = tree?.dataset.path
    const paths = tree ? treePath ? [treePath] : [] : [...selected]
    const entry = tree ? [...folders.values()].flat().find(entry => entry.path === treePath) : selectedEntry
    const targetFolder = menu?.kind === 'context' ? menu.folder : treePath ?? folder
    const key = event.key.toLowerCase()
    if (event.ctrlKey && event.shiftKey && key === 'n') { event.preventDefault(); setMenu(null); props.onCreateFolder(targetFolder) }
    else if (event.ctrlKey && key === 'a' && !tree) { event.preventDefault(); setSelected(new Set(entries.map(entry => entry.path))) }
    else if (event.ctrlKey && (key === 'x' || key === 'c')) { event.preventDefault(); storeClipboard(paths,key === 'x' ? 'move' : 'copy') }
    else if (event.ctrlKey && key === 'v') { event.preventDefault(); setMenu(null); paste(targetFolder) }
    else if (key === 'f2' && entry) { event.preventDefault(); startRename(entry) }
    else if (key === 'delete' && paths.length) { event.preventDefault(); props.onDelete(paths) }
    else if (key === 'escape') { setRenaming(null); pointer.current = null; setDrag(null); setClipboard(null); setMenu(null) }
  }
  const menuAction = (action: () => void) => {
    setMenu(null)
    const returnFocus = menu?.anchor
    if (returnFocus instanceof HTMLElement) returnFocus.focus()
    else returnFocus?.returnFocus.focus()
    action()
  }
  const exclusionMessage = (entry: FileEntry): Message => {
    const exclusion = item && excludedBy(item, entry.path)
    return exclusion === 'ancestor' ? 'inheritedExclusion' : exclusion === 'support' ? 'supportExclusion' : exclusion ? 'include' : 'exclude'
  }
  const canExclude = (entry: FileEntry) => !props.disabled && !['ancestor', 'support'].includes(item ? excludedBy(item, entry.path) || '' : '')

  const menuPaths = menu?.entry ? selected.has(menu.entry.path) ? [...selected] : [menu.entry.path] : []
  return <section className="panel files-panel" aria-label={t('files')} onKeyDown={shortcut} onClickCapture={event => {
    if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false }
  }}>
    {!item ? <div className="empty-workspace">
      <Logo /><h2>{t('emptyTitle')}</h2><p>{t('emptyBody')}</p>
      <button className="primary" type="button" disabled={props.disabled} onClick={props.onNewPackage}><Icon name="plus" />{t('addPackage')}</button>
    </div> : <>
      <div className="workbench-title">
        <div className="workbench-heading"><h2>{item.name || t('unnamed')}</h2><p>{displaySku(item.sku, t)} · Part {displayPart(item.part)}</p></div>
        <StatusButton preview={preview} name={item.name} t={t} onClick={props.onValidation} />
      </div>
      <div className="tabs" role="tablist" aria-label={t('content')} onKeyDown={event => {
        const tab = event.key === 'Home' ? 'files' : event.key === 'End' ? 'preview' :
          event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? (props.tab === 'files' ? 'preview' : 'files') : null
        if (tab) { event.preventDefault(); props.onTab(tab); document.getElementById(`tab-${tab}`)?.focus() }
      }}>
        {(['files', 'preview'] as const).map(tab => <button key={tab} type="button" role="tab" id={`tab-${tab}`}
          aria-controls={`tabpanel-${tab}`} aria-selected={props.tab === tab} tabIndex={props.tab === tab ? 0 : -1} onClick={() => { setMenu(null); props.onTab(tab) }}>
          <Icon name={tab === 'files' ? 'folder' : 'archive'} />{t(tab)}
        </button>)}
      </div>
      <div className="tab-body" role="tabpanel" id={`tabpanel-${props.tab}`} aria-labelledby={`tab-${props.tab}`}>
        {props.tab === 'preview' ? <div className="panel-scroll preview-body">
          <div className="preview-heading"><h3>{t('outputPackage')}</h3><span className="neutral-pill">Part {displayPart(item.part)}</span></div>
          <output id="field-output" tabIndex={-1} className="preview-filename">{preview?.filename ?? t('incomplete')}</output>
          <p className="muted path">{props.output ?? t('unsetOutput')}</p>
          {preview?.stale && <p className="warning" role="status">{t('previewStale')} <button type="button" className="text" onClick={props.onRefresh}>{t('refresh')}</button></p>}
          {preview && <ArchiveEntries entries={preview.entries} t={t} />}
          <p className="muted preview-note">{t(item.cleanSupport ? 'cleanSupportHelp' : 'keepSupportHelp')}{preview && ` · ${t('previewSize', { size: sizeText(preview.size, props.language) })}`}</p>
        </div> : <div className="file-browser" data-drop-target="files" onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={() => { pointer.current=null; setDrag(null) }} id="field-content" tabIndex={0}>
          <div className="file-bar">
            <nav className="breadcrumbs" aria-label={t('breadcrumbs')}>
              <Icon name="folderopen" /><button type="button" className="text" data-drop-folder="" data-drop-active={drag?.target === ''} onClick={() => navigate('')} aria-current={!folder ? 'location' : undefined}>Content</button>
              {crumbs.map((label, index) => <span key={index}><Icon name="chevron" /><button type="button" className="text truncate" data-drop-folder={crumbs.slice(0,index+1).join('/')} data-drop-active={drag?.target === crumbs.slice(0,index+1).join('/')} title={label}
                aria-current={index === crumbs.length - 1 ? 'location' : undefined} onClick={() => navigate(crumbs.slice(0, index + 1).join('/'))}>{label}</button></span>)}
            </nav>
            <button type="button" className="ghost small" aria-label={t('explorer')} title={t('explorer')} onClick={() => props.onOpenFolder(folder)}><Icon name="external" /><span className="button-text">{t('explorerShort')}</span></button>
            <button type="button" className="icon-btn" onClick={props.onRefresh} title={t('refresh')} aria-label={t('refresh')}><Icon name="refresh" /></button>
          </div>
          {errors.size > 0 && <p className="file-problem error" role="alert"><Icon name="error" />{errorText(errors.values().next().value, t)}</p>}
          <div className="file-content">
            <DirectoryTree folders={folders} folder={folder} expanded={expanded} t={t} onToggle={toggle} onFolder={navigate}
              dropTarget={drag?.target} onDragStart={pointerStart} onContext={(event, path, entry) => context(event, path, entry)} />
            <div className="file-table-wrap" data-drop-folder={folder} data-drop-active={drag?.target === folder} tabIndex={0} aria-label={t('fileList')} onContextMenu={event => context(event, folder)}>
              <table className="file-table" aria-label={t('fileList')} aria-busy={loading}>
                <thead><tr><th>{t('fileName')}</th><th>{t('type')}</th><th>{t('size')}</th><th><span className="sr-only">{t('excluded')}</span></th></tr></thead>
                <tbody>{entries.map(entry => {
                  const exclusion = excludedBy(item, entry.path)
                  return <tr key={entry.path} className={exclusion ? 'excluded' : ''} aria-selected={selected.has(entry.path)} data-active={selected.has(entry.path)} data-cut={clipboard?.mode === 'move' && clipboard.paths.some(path => containsPath(path,entry.path))} data-drop-folder={entry.directory ? entry.path : undefined} data-drop-active={entry.directory && drag?.target === entry.path} tabIndex={0}
                    onPointerDown={event => pointerStart(event,entry)}
                    onClick={event => { if (suppressClick.current) { suppressClick.current=false; return }; if (!(event.target as HTMLElement).closest('button,input')) select(entry.path,event.ctrlKey,event.shiftKey) }}
                    onDoubleClick={event => { if (!(event.target as HTMLElement).closest('button,input')) entry.directory ? navigate(entry.path) : props.onDetail(entry) }}
                    onKeyDown={event => {
                      if (event.target !== event.currentTarget) return
                      if (event.key === 'Enter') { event.preventDefault(); entry.directory ? navigate(entry.path) : props.onDetail(entry) }
                      if (event.key === ' ') { event.preventDefault(); select(entry.path,event.ctrlKey,event.shiftKey) }
                      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); const next = entries[entries.indexOf(entry)+(event.key === 'ArrowDown' ? 1 : -1)]; if (next) { const rows = [...event.currentTarget.parentElement!.children] as HTMLElement[]; rows[entries.indexOf(next)]?.focus(); if (!event.ctrlKey || event.shiftKey) select(next.path,event.ctrlKey,event.shiftKey) } }
                      if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') {
                        event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect()
                        if (!selected.has(entry.path)) setSelected(new Set([entry.path])); setMenu({kind:'context', folder:entry.directory ? entry.path : folder, entry, anchor:{x:rect.left+20,y:rect.top+20,returnFocus:event.currentTarget}})
                      }
                    }}
                    onContextMenu={event => context(event, entry.directory ? entry.path : folder, entry)}>
                    <td>{renaming?.path === entry.path ? <div className="inline-rename">
                      <input ref={renameInput} aria-label={t('renameFile')} autoFocus value={renaming.name} disabled={renameBusy} maxLength={255}
                        onFocus={event => { const dot = entry.directory ? -1 : event.currentTarget.value.lastIndexOf('.'); event.currentTarget.setSelectionRange(0,dot > 0 ? dot : event.currentTarget.value.length) }}
                        onChange={event => setRenaming({...renaming,name:event.target.value,error:undefined})}
                        onKeyDown={event => {
                          event.stopPropagation()
                          if (event.key === 'Enter') { event.preventDefault(); void finishRename() }
                          if (event.key === 'Escape') { event.currentTarget.closest('tr')?.focus(); setRenaming(null) }
                        }} />
                      {(renaming.error ?? nameValidation.result ?? nameValidation.error) != null && <span className="error" role="alert">{errorText(renaming.error ?? nameValidation.result ?? nameValidation.error,t)}</span>}
                    </div> : <span className={`file-name ${entry.directory ? '' : 'file'}`} title={entry.name}>
                      <Icon name={entry.directory ? 'folder' : /^(JPG|JPEG|PNG|WEBP)$/i.test(entry.kind) ? 'image' : 'file'} /><span className="truncate">{entry.name}</span>
                    </span>}</td>
                    <td>{entry.directory ? t('folder') : entry.kind}</td><td>{entry.directory ? '—' : sizeText(entry.size, props.language)}</td>
                    <td><button type="button" className="icon-btn" disabled={!canExclude(entry)}
                      onClick={() => props.onExclude(entry.path)} title={t(exclusionMessage(entry), { name: entry.name })} aria-label={t(exclusionMessage(entry), { name: entry.name })}><Icon name={exclusion ? 'eyeoff' : 'eye'} /></button></td>
                  </tr>
                })}</tbody>
              </table>
              {!entries.length && <p className="empty">{t(loading ? 'checking' : 'noFiles')}</p>}
            </div>
          </div>
          <div className="selection-tools">
            <span className="muted truncate">{selected.size ? selectedEntry?.name ?? t('selected',{count:selected.size}) : t('entryCount', { count: entries.length })}</span>
            <button type="button" className="ghost small" disabled={!selectedEntry} onClick={() => selectedEntry && props.onDetail(selectedEntry)}><Icon name="info" />{t('detail')}</button>
            <button type="button" className="ghost small" disabled={props.disabled} aria-haspopup="menu" aria-expanded={menu?.kind === 'add'}
              onClick={event => setMenu(menu?.kind === 'add' ? null : { anchor:event.currentTarget,kind:'add',folder })}><Icon name="plus" />{t('addContent')}<Icon name="down" /></button>
          </div>
          {props.archiveDrop&&!props.disabled&&<div className="archive-drop-zone" data-drop-target="archive" data-active={props.archiveDrop.active} data-valid={props.archiveDrop.valid}>
            <Icon name="archive"/><div><strong>{t('archiveDropZone',{name:item.name||t('unnamed')})}</strong><span>Content · ZIP, RAR, 7z</span></div>
          </div>}
          <div className="file-footer"><span><Icon name="folder" />{t('content')} · Part {displayPart(item.part)}</span><span>{t('dropFiles')}</span></div>
        </div>}
      </div>
      {drag && <div className="file-drag-label" role="status" style={{left:drag.x+14,top:drag.y+14}}>{drag.target === null ? t('invalidDrop') : t('movingFiles',{count:pointer.current?.paths.length ?? 0,path:drag.target ? '/' + drag.target : ''})}</div>}
      {menu && !props.disabled && <Popup anchor={menu.anchor} menu label={t(menu.kind === 'add' ? 'addContent' : 'fileActions')} onClose={() => setMenu(null)}>
        {menu.kind === 'context' && menu.entry?.directory && <button type="button" role="menuitem" onClick={() => menuAction(() => navigate(menu.entry!.path))}><Icon name="folderopen" />{t('open')}</button>}
        {(menu.kind === 'add' || !menu.entry || menu.entry.directory) && <>
          <button type="button" role="menuitem" disabled={props.disabled} onClick={() => menuAction(() => props.onAdd(false, menu.folder))}><Icon name="file" />{t('addFiles')}</button>
          <button type="button" role="menuitem" disabled={props.disabled} onClick={() => menuAction(() => props.onAdd(true, menu.folder))}><Icon name="folder" />{t('addFolders')}</button>
          {props.onImport&&<button type="button" role="menuitem" disabled={props.disabled} onClick={()=>menuAction(props.onImport!)}><Icon name="archive"/>{t('archiveExtract')}</button>}
        </>}
        {menu.kind === 'context' && <>
          {!!menuPaths.length && <>
            <button type="button" role="menuitem" onClick={() => menuAction(() => storeClipboard(menuPaths,'move'))}><Icon name="cut" />{t('cutFiles')}<kbd>Ctrl+X</kbd></button>
            <button type="button" role="menuitem" onClick={() => menuAction(() => storeClipboard(menuPaths,'copy'))}><Icon name="copy" />{t('copyFiles')}<kbd>Ctrl+C</kbd></button>
          </>}
          {(!menu.entry || menu.entry.directory) && <button type="button" role="menuitem" disabled={!clipboard || clipboard.id !== item.id || menuPaths.length > 1} onClick={() => menuAction(() => paste(menu.folder))}><Icon name="paste" />{t('pasteFiles')}<kbd>Ctrl+V</kbd></button>}
          {menu.entry && menuPaths.length === 1 && <button type="button" role="menuitem" onClick={() => menuAction(() => startRename(menu.entry!))}><Icon name="rename" />{t('renameFile')}<kbd>F2</kbd></button>}
          {!!menuPaths.length && <button type="button" role="menuitem" className="danger" onClick={() => menuAction(() => props.onDelete(menuPaths))}><Icon name="trash" />{t('deleteFiles')}<kbd>Del</kbd></button>}
          <div role="separator" />
          {(!menu.entry || menu.entry.directory) && <button type="button" role="menuitem" disabled={props.disabled} onClick={() => menuAction(() => props.onCreateFolder(menu.folder))}><Icon name="plus" />{t('newFolder')}<kbd>Ctrl+Shift+N</kbd></button>}
          {menu.entry && menuPaths.length === 1 && <>
            <button type="button" role="menuitem" disabled={!canExclude(menu.entry)} onClick={() => menuAction(() => props.onExclude(menu.entry!.path))}><Icon name={excludedBy(item, menu.entry.path) ? 'eye' : 'eyeoff'} />{t(exclusionMessage(menu.entry), { name: menu.entry.name })}</button>
            <button type="button" role="menuitem" onClick={() => menuAction(() => props.onDetail(menu.entry!))}><Icon name="info" />{t('detail')}</button>
          </>}
          <div role="separator" />
          <button type="button" role="menuitem" onClick={() => menuAction(() => props.onOpenFolder(menu.folder))}><Icon name="external" />{t('explorer')}</button>
          <button type="button" role="menuitem" onClick={() => menuAction(props.onRefresh)}><Icon name="refresh" />{t('refresh')}</button>
        </>}
      </Popup>}
    </>}
  </section>
}

export function DirectoryTree({ folders, folder, expanded, t, onToggle, onFolder, onContext, onDragStart, dropTarget }: {
  folders: ReadonlyMap<string, FileEntry[]>
  folder: string
  expanded: ReadonlySet<string>
  t: Translate
  onToggle: (path: string) => void
  onFolder: (path: string) => void
  onDragStart?: (event: PointerEvent<HTMLElement>, entry: FileEntry) => void
  dropTarget?: string | null
  onContext: (event: MouseEvent<HTMLElement>, path: string, entry?: FileEntry) => void
}) {
  const [focused, setFocused] = useState('')
  const visible: string[] = []
  const collect = (path: string) => {
    visible.push(path)
    if (expanded.has(path)) folders.get(path)?.filter(entry => entry.directory).forEach(entry => collect(entry.path))
  }
  collect('')
  const focusPath = visible.includes(focused) ? focused : ''
  const render = (path: string, label: string, depth: number, entry?: FileEntry) => {
    const children = folders.get(path)?.filter(entry => entry.directory)
    const expandable = children === undefined || children.length > 0
    const open = expanded.has(path)
    return <div key={path} role="none">
      <div role="treeitem" className="tree-folder" data-path={path} data-drop-folder={path} data-drop-active={dropTarget === path} onPointerDown={event => entry && onDragStart?.(event,entry)} data-active={path === folder} aria-label={label}
        aria-level={depth + 1} aria-expanded={expandable ? open : undefined} aria-selected={path === folder} tabIndex={path === focusPath ? 0 : -1}
        style={{ paddingInlineStart: 6 + Math.max(0, depth - 1) * 16 }} title={`Content${path ? '/' + path : ''}`}
        onFocus={() => setFocused(path)} onClick={() => onFolder(path)} onContextMenu={event => onContext(event, path, entry)}
        onKeyDown={event => {
          if (!['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End','Enter',' '].includes(event.key)) return
          event.stopPropagation()
          const elements = [...event.currentTarget.closest('[role="tree"]')!.querySelectorAll<HTMLElement>('[role="treeitem"]')]
          const index = elements.indexOf(event.currentTarget)
          const focus = (next: number) => elements[next]?.focus()
          if (event.key === 'ArrowDown') { event.preventDefault(); focus(index + 1) }
          else if (event.key === 'ArrowUp') { event.preventDefault(); focus(index - 1) }
          else if (event.key === 'Home') { event.preventDefault(); focus(0) }
          else if (event.key === 'End') { event.preventDefault(); focus(elements.length - 1) }
          else if (event.key === 'ArrowRight') { event.preventDefault(); if (expandable && !open) onToggle(path); else if (children?.length) focus(index + 1) }
          else if (event.key === 'ArrowLeft') {
            event.preventDefault()
            if (expandable && open) onToggle(path)
            else elements.find(element => element.dataset.path === path.split('/').slice(0, -1).join('/'))?.focus()
          } else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onFolder(path) }
        }}>
        <button type="button" className="tree-toggle" tabIndex={-1} disabled={!expandable} aria-label={t(open ? 'collapseFolder' : 'expandFolder', { name: label })}
          onClick={event => { event.stopPropagation(); event.currentTarget.parentElement?.focus(); onToggle(path) }}><Icon name={open ? 'down' : 'chevron'} /></button>
        <Icon name={path === folder ? 'folderopen' : 'folder'} /><span className="truncate">{label}</span>
      </div>
      {open && !!children?.length && <div role="group">{children.map(child => render(child.path, child.name, depth + 1, child))}</div>}
    </div>
  }
  return <div className="folder-tree" role="tree" aria-label={t('folders')} onContextMenu={event => onContext(event, folder)}>{render('', 'Content', 0)}</div>
}

function ArchiveEntries({ entries, t }: { entries: string[]; t: Translate }) {
  const [limit, setLimit] = useState(200)
  const paths = new Set<string>()
  entries.slice(0, limit).forEach(entry => {
    const parts = entry.split('/')
    parts.forEach((_, index) => paths.add(parts.slice(0, index + 1).join('/')))
  })
  return <>
    <ul className="archive-tree" aria-label={t('content')}>
      {[...paths].map(path => <li key={path} title={path} style={{ paddingInlineStart: (path.split('/').length - 1) * 18 }}>
        <Icon name={entries.includes(path) && !path.endsWith('/') ? 'file' : 'folder'} /><span>{path.split('/').pop()}</span>
      </li>)}
    </ul>
    {entries.length > limit && <div className="archive-footer"><span className="muted">{t('archiveEntries', { count: limit, total: entries.length })}</span>
      <button type="button" className="small" onClick={() => setLimit(current => current + 200)}>{t('showMore')}</button></div>}
  </>
}
