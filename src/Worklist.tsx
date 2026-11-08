import { useEffect, useRef, useState, type MouseEvent, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import type { Package, Preview, WorklistAction, WorklistEntry, WorklistSource, WorklistState } from './api'
import { sizeText, type Translate } from './messages'
import { displayPart, displaySku, packageTargets, selectPackages } from './workflow'
import { canGroup, canNudge, entryIds } from './worklistView'
import { useWorklistDrag } from './useWorklistDrag'
import { Icon } from './Icon'
import { Popup, type PopupAnchor } from './Popup'
import { StatusButton } from './StatusButton'

type Props = {
  nativeDrop?: 'valid' | 'invalid'
  packages: Package[]; visible: Package[]; active: string | null; previews: Record<string, Preview>
  worklist: WorklistState; selected: ReadonlySet<string>; query: string; t: Translate; language: string; disabled: boolean
  onQuery: (query: string) => void; onActive: (id: string) => void; onCheck: (id: string, checked: boolean) => void
  onCheckAll: (checked: boolean) => void; onClear: () => void; onAdd: () => void; onValidate: (id: string) => void
  onSelection: (selected: ReadonlySet<string>) => void; onBuild: (ids: string[]) => void; onRemove: (ids: string[]) => void
  onSynchronize?: (group: string) => void
  onImport?: () => void
  onStructure: (action?: WorklistAction) => Promise<boolean | undefined>
}
type Menu = {id: string; ids: string[]; group: boolean; anchor: PopupAnchor}

export function Worklist(props: Props) {
  const { t, packages, visible, selected, worklist } = props
  const list = useRef<HTMLDivElement>(null)
  const [anchor, setAnchor] = useState<string | null>(null)
  const [menu, setMenu] = useState<Menu | null>(null)
  const [notice, setNotice] = useState(false)
  const order = packages.map(item => item.id)
  const visibleIds = new Set(visible.map(item => item.id))
  const byId = new Map(packages.map(item => [item.id,item]))
  const marked = order.filter(id => selected.has(id))
  const searching = !!props.query.trim()
  const structureDisabled = props.disabled || !!worklist.saveError || worklist.warnings.some(warning=>warning.code==='syncRecoveryRequired')
  const membership = order.join('|')
  useEffect(() => { setAnchor(null); setMenu(null) }, [props.query])
  useEffect(() => { setMenu(null); setAnchor(current => current && order.includes(current) ? current : null) }, [membership, worklist.revision])
  useEffect(() => { if (props.disabled) setMenu(null) }, [props.disabled])
  useEffect(() => { if (!worklist.canUndo) setNotice(false) }, [worklist.canUndo])
  const structure = async (action?: WorklistAction) => {
    if (structureDisabled) return
    setMenu(null)
    const focused = document.activeElement?.closest<HTMLElement>('[data-package-id], [data-group-id]')
    const focusId = focused?.dataset.packageId ?? focused?.dataset.groupId
    const changed = await props.onStructure(action)
    if (changed && action && action.kind !== 'collapse') setNotice(true)
    if (changed && !action) setNotice(false)
    requestAnimationFrame(() => {
      const row = [...(list.current?.querySelectorAll<HTMLElement>('[data-package-id], [data-group-id]') ?? [])]
        .find(row => row.dataset.packageId === focusId || row.dataset.groupId === focusId)
      ;(row?.querySelector<HTMLElement>('.package-link, .group-toggle') ?? list.current)?.focus()
    })
  }
  const {drag,start,suppressClick} = useWorklistDrag(list, structureDisabled || searching, worklist.revision, action => void structure(action))
  const open = (event: MouseEvent, id: string) => {
    if (props.disabled) return
    if (suppressClick.current) { suppressClick.current = false; return }
    if (event.shiftKey || event.ctrlKey) {
      event.preventDefault()
      props.onSelection(selectPackages(selected, visible.map(item => item.id), anchor, id, event.shiftKey ? event.ctrlKey ? 'addRange' : 'range' : 'toggle'))
      if (!event.shiftKey || !anchor) setAnchor(id)
    } else { setAnchor(id); props.onActive(id) }
  }
  const context = (event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>, id: string, group: boolean) => {
    event.preventDefault(); event.stopPropagation()
    if (props.disabled || drag) return
    const button = (event.target as HTMLElement).closest<HTMLElement>('button:not(:disabled), input:not(:disabled)')
      ?? event.currentTarget.querySelector<HTMLElement>(group ? '.group-toggle:not(:disabled), .group-gutter input:not(:disabled)' : '.package-link')!
    button.focus()
    const rect = button.getBoundingClientRect()
    const entry = worklist.entries.find(entry => entry.id === id)
    setMenu({id,group,ids:group && entry ? entryIds(entry).filter(id => byId.has(id)) : packageTargets(selected,order,id),anchor:event.type === 'click' ? button : {
      x:'clientX' in event ? event.clientX : rect.left+16, y:'clientY' in event ? event.clientY : rect.bottom, returnFocus:button,
    }})
  }
  const menuAction = (action: () => void) => {
    if (menu) (menu.anchor instanceof HTMLElement ? menu.anchor : menu.anchor.returnFocus).focus()
    setMenu(null); action()
  }
  const selectGroup = (ids: string[], checked: boolean) => {
    const next = new Set(selected); for (const id of ids) { if (checked) next.add(id); else next.delete(id) }
    props.onSelection(next)
  }
  const renderPackage = (item: Package, standalone = false) => {
    const preview = props.previews[item.id]
    const name = item.name || t('unnamed')
    return <div key={item.id} className={`package-row${standalone ? ' standalone' : ''}`} data-worklist-block={standalone ? item.id : undefined}
      data-package-id={item.id} data-active={props.active === item.id} data-checked={selected.has(item.id)} data-drag-source={drag?.source.kind === 'packages' && drag.ids.includes(item.id)} data-drop-highlight={drag?.drop?.highlight === item.id}
      onContextMenu={event => context(event,item.id,false)} onKeyDown={event => {
        if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') context(event,item.id,false)
      }}>
      <input type="checkbox" checked={selected.has(item.id)} disabled={props.disabled} aria-label={t('selectPackage',{name})} onChange={event => { setAnchor(item.id); props.onCheck(item.id,event.target.checked) }} />
      <button type="button" className="package-link" disabled={props.disabled} onClick={event => open(event,item.id)} aria-current={props.active === item.id}
        onPointerDown={event => { const ids = selected.has(item.id) ? marked : [item.id]; start(event,{kind:'packages',ids},ids) }}>
        <strong title={name}>{name}</strong>
        <small><span>{displaySku(item.sku,t)}</span><span>Part {displayPart(item.part)}</span>{preview && <span>{sizeText(preview.size,props.language)}</span>}</small>
      </button>
      <StatusButton preview={preview} t={t} compact name={name} onClick={() => props.onValidate(item.id)} />
    </div>
  }
  const renderGroup = (entry: Extract<WorklistEntry,{kind:'group'}>) => {
    const members = entry.members.flatMap(id => { const item = byId.get(id); return item ? [item] : [] })
    const matches = members.filter(item => visibleIds.has(item.id))
    if (!matches.length) return null
    const ids = matches.map(item => item.id)
    const checked = ids.filter(id => selected.has(id)).length
    const collapsed = entry.collapsed && !searching
    const first = members[0].name || t('unnamed')
    const groupLabel = t('groupSummary',{name:first,count:members.length})
    const toggle = () => void structure({kind:'collapse',group:entry.id,collapsed:!entry.collapsed})
    const groupMenu = <button type="button" className="icon-btn group-more" disabled={props.disabled} title={t('groupActions')} aria-label={t('groupActions')} onClick={event => context(event,entry.id,true)}><Icon name="more" /></button>
    return <section key={entry.id} className={`group-block${collapsed ? ' group-collapsed' : ''}`} aria-label={groupLabel}
      data-worklist-block={entry.id} data-group-id={entry.id} data-active={entry.members.includes(props.active ?? '')}
      data-drag-source={drag?.source.kind === 'group' && drag.source.id === entry.id} data-drop-highlight={drag?.drop?.highlight === entry.id}
      onContextMenu={event => context(event,entry.id,true)} onKeyDown={event => {
        if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') context(event,entry.id,true)
      }}>
      <div className="group-gutter">
        <button type="button" className="icon-btn group-toggle" disabled={structureDisabled || searching} onClick={toggle} aria-expanded={!collapsed} title={t(collapsed?'expandGroup':'collapseGroup')} aria-label={t(collapsed?'expandGroup':'collapseGroup')}><Icon name={collapsed?'chevron':'down'} /></button>
        <input type="checkbox" checked={checked===ids.length} ref={element => { if (element) element.indeterminate = checked>0 && checked<ids.length }} disabled={props.disabled}
          aria-label={t('selectGroup',{name:first})} onChange={event => selectGroup(ids,event.target.checked)} />
        <button type="button" className="icon-btn group-grip" disabled={structureDisabled || searching} aria-label={t('moveGroup')} title={t('moveGroup')}
          onPointerDown={event => start(event,{kind:'group',id:entry.id},members.map(item => item.id))}><Icon name="grip" /></button>
        {!collapsed && groupMenu}
      </div>
      {collapsed ? <div className="group-summary"><button type="button" className="summary-main" onClick={toggle} disabled={structureDisabled}>
        <strong className="summary-title">{first}</strong><small className="summary-meta"><span className="summary-badge">{t('morePackages',{count:members.length-1})}</span><span>{sizeText(members.reduce((sum,item) => sum+(props.previews[item.id]?.size ?? 0),0),props.language)}</span></small>
      </button>{groupMenu}</div> : <div className="group-members">{matches.map(item => renderPackage(item))}
        {searching && matches.length<members.length && <p className="filter-note">{t('groupSearchMatches',{count:matches.length,total:members.length})}</p>}
      </div>}
    </section>
  }
  const checked = visible.filter(item => selected.has(item.id)).length
  const menuSource: WorklistSource | null = menu ? menu.group ? {kind:'group',id:menu.id} : {kind:'packages',ids:menu.ids} : null
  const menuEntry = menu?.group ? worklist.entries.find(entry => entry.id===menu.id) : null
  const dragHint = drag?.drop?.target.kind === 'combine' ? 'dropCombine' : drag?.drop?.target.kind === 'join' ? drag.source.kind === 'group' ? 'dropMerge' : 'dropJoin' : drag?.drop?.target.kind === 'member' ? 'dropMember' : drag?.drop ? 'dropRoot' : 'dropInvalid'
  return <aside data-drop-target="worklist" data-native-drop={props.nativeDrop} className="panel worklist" data-dragging={!!drag} aria-label={t('list')} onKeyDown={event => {
    if (props.disabled || (event.target as HTMLElement).closest('input:not([type="checkbox"]),textarea,select,[contenteditable="true"],[role="menu"]')) return
    if (event.ctrlKey && event.key.toLowerCase()==='a') { event.preventDefault(); props.onCheckAll(true) }
    if (event.key==='Delete' && marked.length) { event.preventDefault(); props.onRemove(marked) }
    if (event.ctrlKey && !event.shiftKey && event.key.toLowerCase()==='z' && worklist.canUndo) { event.preventDefault(); void structure() }
    if (event.altKey && ['ArrowUp','ArrowDown'].includes(event.key) && !searching) {
      event.preventDefault()
      const row = (event.target as HTMLElement).closest<HTMLElement>('[data-package-id]')
      const group = (event.target as HTMLElement).closest<HTMLElement>('[data-group-id]')
      const source: WorklistSource | null = row ? {kind:'packages',ids:packageTargets(selected,order,row.dataset.packageId!)} : group ? {kind:'group',id:group.dataset.groupId!} : null
      const up = event.key==='ArrowUp'
      if (source && canNudge(worklist.entries,source,up)) void structure({kind:'nudge',source,up})
    }
  }}>
    <div className="panel-title"><h2>{t('list')}</h2><div className="worklist-actions">
      {props.onImport&&<button type="button" className="small" title={t('archiveTitle')} aria-label={t('archiveTitle')} disabled={structureDisabled} onClick={props.onImport}><Icon name="archive"/>{t('archiveImportAction')}</button>}
      <button type="button" className="small" aria-label={t('addPackage')} disabled={props.disabled || !!worklist.saveError || worklist.warnings.some(warning=>warning.code==='syncRecoveryRequired')} onClick={props.onAdd}><Icon name="plus" />{t('product')}</button>
    </div></div>
    <div className="worklist-tools"><div className="search-row"><Icon name="search" /><input type="search" value={props.query} onChange={event => props.onQuery(event.target.value)} placeholder={t('findPackage')} aria-label={t('findPackage')} /></div>
      <div className="selectionbar"><label className="select-all"><input type="checkbox" checked={!!visible.length && checked===visible.length} disabled={props.disabled || !visible.length}
        ref={element => { if (element) element.indeterminate = checked>0 && checked<visible.length }} onChange={event => props.onCheckAll(event.target.checked)} />{t(searching?'selectSearchResults':'selectAll')}</label>
        <button type="button" className="small group-action" disabled={structureDisabled || !canGroup(worklist.entries,marked)} onClick={() => void structure({kind:'group',ids:marked})}><Icon name="group" />{t('groupPackages')}</button>
      </div>
    </div>
    <div ref={list} className="panel-scroll package-list" tabIndex={0} aria-label={t('list')}>
      {worklist.entries.map(entry => entry.kind==='group' ? renderGroup(entry) : visibleIds.has(entry.id) && byId.has(entry.id) ? renderPackage(byId.get(entry.id)!,true) : null)}
      {!visible.length && <p className="empty">{t(packages.length?'noMatch':'emptyList')}</p>}
    </div>
    {drag && <div className="worklist-end" data-drop-highlight={drag.drop?.target.kind==='root' && !drag.drop.target.anchor}><Icon name="ungroup" /><span>{t('dropIndependent')}</span></div>}
    <div className="panel-footer"><span>{searching?t('matchingPackages',{count:visible.length,total:packages.length}):t('allPackages',{count:packages.length})}</span>
      {!searching && worklist.entries.some(entry => entry.kind==='group') && <span>· {t('groupCount',{count:worklist.entries.filter(entry => entry.kind==='group' && entry.members.some(id => byId.has(id))).length})}</span>}
      {!!selected.size && <button className="text small" type="button" onClick={() => { setAnchor(null); props.onClear() }}>{t('clearSelection')} · {t('selected',{count:selected.size})}</button>}
    </div>
    {menu && <Popup anchor={menu.anchor} menu label={t(menu.group?'groupActions':'packageActions')} onClose={() => setMenu(null)}>
      {menu.group && menuEntry?.kind==='group' ? <>
        <button type="button" role="menuitem" disabled={structureDisabled || searching} onClick={() => menuAction(() => void structure({kind:'collapse',group:menu.id,collapsed:!menuEntry.collapsed}))}><Icon name={menuEntry.collapsed?'chevron':'down'} />{t(menuEntry.collapsed?'expandGroup':'collapseGroup')}</button>
        <button type="button" role="menuitem" onClick={() => menuAction(() => selectGroup(menu.ids,true))}><Icon name="check" />{t('selectGroupAll')}</button>
        {props.onSynchronize && <button type="button" role="menuitem" disabled={structureDisabled} onClick={() => menuAction(() => props.onSynchronize?.(menu.id))}><Icon name="refresh" />{t('syncMenu')}</button>}
      </> : <button type="button" role="menuitem" onClick={() => menuAction(() => { setAnchor(menu.id); props.onActive(menu.id) })}><Icon name="folderopen" />{t('openSelectedPackage')}</button>}
      {!menu.group && <button type="button" role="menuitem" disabled={structureDisabled || !canGroup(worklist.entries,menu.ids)} onClick={() => menuAction(() => void structure({kind:'group',ids:menu.ids}))}><Icon name="group" />{t('groupPackages')}</button>}
      {!menu.group && menu.ids.some(id => worklist.entries.some(entry => entry.kind==='group' && entry.members.includes(id))) && <button type="button" role="menuitem" disabled={structureDisabled} onClick={() => menuAction(() => void structure({kind:'detach',ids:menu.ids}))}><Icon name="ungroup" />{t('detachPackages')}</button>}
      <button type="button" role="menuitem" disabled={!menu.ids.length} onClick={() => menuAction(() => props.onBuild(menu.ids))}><Icon name="play" />{t(menu.group?'buildGroup':'buildSelected',{count:menu.ids.length})}</button>
      <div role="separator" />
      {[true,false].map(up => <button key={String(up)} type="button" role="menuitem" disabled={structureDisabled || searching || !menuSource || !canNudge(worklist.entries,menuSource,up)} onClick={() => menuAction(() => void structure({kind:'nudge',source:menuSource!,up}))}><Icon name={up?'up':'down'} />{t(up?'moveUp':'moveDown')}<kbd>{up?'Alt+↑':'Alt+↓'}</kbd></button>)}
      <div role="separator" />
      {menu.group ? <button type="button" role="menuitem" disabled={structureDisabled} onClick={() => menuAction(() => void structure({kind:'dissolve',group:menu.id}))}><Icon name="ungroup" />{t('dissolveGroup')}</button> : <button type="button" role="menuitem" className="danger" onClick={() => menuAction(() => props.onRemove(menu.ids))}><Icon name="trash" />{t('removePackages',{count:menu.ids.length})}</button>}
    </Popup>}
    {notice && worklist.canUndo && createPortal(<div className="worklist-notice" role="status">{t('worklistUpdated')}<button type="button" className="text" disabled={structureDisabled} onClick={() => void structure()}><Icon name="undo" />{t('undoWorklist')}</button><button type="button" className="icon-btn" aria-label={t('close')} onClick={() => setNotice(false)}><Icon name="close" /></button></div>,document.body)}
    {drag && createPortal(<><div className="worklist-drag-ghost" style={{left:Math.min(drag.x+16,window.innerWidth-295),top:Math.min(drag.y+16,window.innerHeight-80)}} aria-hidden="true"><Icon name={drag.source.kind==='group'?'group':'archive'} /><div><strong>{t('dragPackages',{count:drag.ids.length})}</strong><small>{t(dragHint)}</small></div></div>
      {drag.drop?.line && <div className="worklist-drop-line" style={drag.drop.line} />}
      <span className="sr-only" role="status">{t('dragPackages',{count:drag.ids.length})} · {t(dragHint)}</span></>,document.body)}
  </aside>
}
