import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { api, type Package, type SyncField, type SyncPreview, type SyncRequest, type SyncResult, type SyncSession } from './api'
import { Modal } from './dialogs'
import { SingleSelect } from './SingleSelect'
import { useValidation } from './useValidation'
import { errorText, type Message, type Translate } from './messages'
import { displayPart, displaySku } from './workflow'
import { Icon } from './Icon'

const fields: {id: SyncField; label: Message}[] = [
  {id:'store',label:'syncStore'}, {id:'sku',label:'sku'}, {id:'tags',label:'tags'},
  {id:'name',label:'name'}, {id:'cover',label:'cover'}, {id:'support',label:'syncSupport'},
]
type Drop = {id: string; before: boolean}
type Drag = {id: string; pointer: number; element: HTMLElement; x: number; y: number; active: boolean; drop: Drop | null}

export function GroupSyncDialog({session, source: initialSource, t, onClose, onReload, onApply}: {
  session: SyncSession; source: string; t: Translate; onClose: () => void; onReload: () => void
  onApply: (request: SyncRequest, preview: SyncPreview) => Promise<SyncResult>
}) {
  const {packages} = session.data
  const [source, setSource] = useState(initialSource)
  const [targets, setTargets] = useState(packages.filter(p => p.id !== initialSource).map(p => p.id))
  const [selectedFields, setFields] = useState(fields.map(field => field.id))
  const [order, setOrder] = useState(packages.map(p => p.id))
  const [renumber, setRenumber] = useState(true)
  const [review, setReview] = useState<SyncPreview>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>()
  const [reloadRequired, setReloadRequired] = useState(false)
  const sourcePackage = packages.find(p => p.id === source)!
  const [cover, setCover] = useState<{id: string; file: string; url: string | null; error?: unknown}>()
  const sourceCover = cover?.id === source && cover.file === sourcePackage.cover ? cover : undefined
  const body = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const [dragView, setDragView] = useState<Drag | null>(null)
  const frame = useRef(0)
  const request = useMemo<SyncRequest>(() => ({group:session.data.group,source,targets,fields:selectedFields,
    order:renumber ? order : null,expected:session.data.expected}),[session,source,targets,selectedFields,renumber,order])
  const validate = useCallback((value: SyncRequest) => api.previewGroupSync(value,session.context),[session.context])
  const validation = useValidation(request,validate,!review && !busy && !reloadRequired)
  const draftPreview = review ?? validation.result
  const changed = !!draftPreview && (!!draftPreview.changes.length || !!draftPreview.order)
  const failure = error ?? validation.error
  const stale = reloadRequired || !!failure && typeof failure === 'object' && 'code' in failure && failure.code === 'syncStale'
  const name = (p: Package) => p.name || t('unnamed')
  useEffect(() => {
    const file = sourcePackage.cover
    if (!file) return
    let current = true
    void api.getCover(source).then(url => {
      if (current) setCover({id:source,file,url})
    }).catch(error => {
      if (current) setCover({id:source,file,url:null,error})
    })
    return () => { current = false }
  },[source,sourcePackage.cover])
  const cancelDrag = useCallback(() => {
    cancelAnimationFrame(frame.current)
    const previous = drag.current
    drag.current = null
    if (previous?.element.hasPointerCapture?.(previous.pointer)) previous.element.releasePointerCapture(previous.pointer)
    setDragView(null)
  },[])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && drag.current) { event.preventDefault(); event.stopImmediatePropagation(); cancelDrag() }
    }
    document.addEventListener('keydown',escape,true)
    window.addEventListener('blur',cancelDrag)
    return () => { cancelAnimationFrame(frame.current); document.removeEventListener('keydown',escape,true); window.removeEventListener('blur',cancelDrag) }
  },[cancelDrag])
  useEffect(() => {
    if (review) { body.current?.closest('.dialog-body')?.scrollTo?.(0,0); body.current?.focus({preventScroll:true}) }
  },[review])
  const targetAt = (x: number, y: number): Drop | null => {
    const container = list.current, viewport = container?.closest('.dialog-body')?.getBoundingClientRect()
    if (!container || !viewport || y < viewport.top || y > viewport.bottom) return null
    const bounds = container.getBoundingClientRect()
    if (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom) return null
    const rows = [...container.querySelectorAll<HTMLElement>('[data-sync-part]')]
    const row = rows.find(row => y < row.getBoundingClientRect().bottom) ?? rows.at(-1)
    if (!row) return null
    const box = row.getBoundingClientRect()
    return {id:row.dataset.syncPart!,before:y < box.top+box.height/2}
  }
  const reorder = (id: string, drop: Drop) => {
    if (id === drop.id) return
    setOrder(current => {
      const next = current.filter(item => item !== id)
      next.splice(next.indexOf(drop.id)+(drop.before?0:1),0,id)
      return next
    })
  }
  const startDrag = (event: PointerEvent<HTMLDivElement>, id: string) => {
    if (!renumber || busy || event.button !== 0) return
    event.currentTarget.focus()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = {id,pointer:event.pointerId,element:event.currentTarget,x:event.clientX,y:event.clientY,active:false,drop:null}
  }
  const moveDrag = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current
    if (!current || current.pointer !== event.pointerId) return
    if (!current.active && Math.hypot(event.clientX-current.x,event.clientY-current.y)<6) return
    current.active = true; current.x = event.clientX; current.y = event.clientY
    current.drop = targetAt(current.x,current.y)
    setDragView({...current})
    if (frame.current) cancelAnimationFrame(frame.current)
    const scroll = () => {
      const active = drag.current, parts = list.current, container = parts?.closest('.dialog-body')
      if (!active?.active || !parts || !container) return
      const viewport = container.getBoundingClientRect(), bounds = parts.getBoundingClientRect()
      const top = Math.max(bounds.top,viewport.top), bottom = Math.min(bounds.bottom,viewport.bottom)
      const delta = active.y < top+32 ? -9 : active.y > bottom-32 ? 9 : 0
      if (delta) {
        const previous = parts.scrollTop
        parts.scrollTop += delta
        if (parts.scrollTop === previous) container.scrollTop += delta
        active.drop = targetAt(active.x,active.y); setDragView({...active})
      }
      frame.current = requestAnimationFrame(scroll)
    }
    frame.current = requestAnimationFrame(scroll)
  }
  const endDrag = (event: PointerEvent) => {
    const current = drag.current
    if (current?.pointer !== event.pointerId) return
    const drop = targetAt(event.clientX,event.clientY)
    if (current.active && drop) reorder(current.id,drop)
    cancelDrag()
  }
  const check = async () => {
    setBusy(true); setError(undefined)
    try { setReview(await validation.check()) } catch (error) { setError(error) }
    finally { setBusy(false) }
  }
  const apply = async () => {
    if (!review) return
    setBusy(true); setError(undefined)
    try {
      const result = await onApply(request,review)
      if (result.error) { setError(result.error); setReloadRequired(true) }
      else onClose()
    } catch (error) { setError(error) }
    finally { setBusy(false) }
  }
  const value = (p: Package, field: SyncField | 'part', after = false) => {
    switch (field) {
      case 'store': return `${p.store || t('syncEmpty')} · ${p.prefix || t('syncEmpty')} · ${t(p.autoPrefix?'syncAutomatic':'syncManual')}`
      case 'sku': return p.sku || t('syncEmpty')
      case 'tags': return p.tags.join(', ') || t('syncNoTags')
      case 'name': return p.name || t('syncEmpty')
      case 'cover': return t(p.cover ? after ? 'syncCoverReplaced' : 'syncCoverPresent' : 'syncNoCover')
      case 'support': return t(p.cleanSupport?'syncEnabled':'syncDisabled')
      case 'part': return displayPart(p.part)
    }
  }
  return <Modal title={t('syncTitle')} t={t} className={`sync-dialog${dragView ? ' sync-dragging' : ''}`} onClose={busy ? undefined : onClose}
    footer={<>{!review && <small className="sync-count" role="status">{t('syncCount',{count:validation.result?.changes.length ?? 0})}</small>}
      {review ? <button type="button" disabled={busy || reloadRequired} onClick={() => {setReview(undefined);setError(undefined)}}>{t('syncBack')}</button>
        : <button type="button" disabled={busy} onClick={onClose}>{t('cancel')}</button>}
      <button type="button" className="primary" disabled={busy || stale || !changed} onClick={() => void (review ? apply() : check())}>
        {review && <Icon name="check" />}{t(review?'syncApply':'syncReview')}{!review && <span aria-hidden="true">→</span>}</button></>}>
    <div className="sync-kicker" aria-label={t('syncSteps')}>
      <span className={`sync-step${!review?' active':''}`} aria-current={!review?'step':undefined}><span className="number">1</span>{t('syncChoose')}</span><span className="sync-step-line" />
      <span className={`sync-step${review?' active':''}`} aria-current={review?'step':undefined}><span className="number">2</span>{t('syncCheck')}</span>
    </div>
    <div className="sync-body" ref={body} tabIndex={-1} aria-label={t(review?'syncCheck':'syncChoose')}>
      {failure != null && <div className="sync-warning" role="alert"><Icon name="error" /><div>{errorText(failure,t)}{stale && <button type="button" disabled={busy} onClick={onReload}>{t('syncReload')}</button>}</div></div>}
      {!review ? <fieldset disabled={busy}>
        <div className="sync-two-col"><div><h3 className="sync-heading">{t('syncSourceTargets')}</h3>
          <label className="sync-select-label" htmlFor="sync-source">{t('syncSource')}</label>
          <SingleSelect id="sync-source" label={t('syncSource')} disabled={busy} value={source} options={packages.map(p => ({value:p.id,label:`${name(p)} · Part ${displayPart(p.part)}`}))}
            onChange={id => {setSource(id);setTargets(packages.filter(p=>p.id!==id).map(p=>p.id));setError(undefined)}} />
          <div className="sync-section-top sync-targets-heading"><h3 className="sync-heading">{t('syncTargets')}</h3><button type="button" className="text small" onClick={() => setTargets(targets.length===packages.length-1?[]:packages.filter(p=>p.id!==source).map(p=>p.id))}>{t(targets.length===packages.length-1?'syncSelectNone':'selectAll')}</button></div>
          <div className="sync-box">{packages.filter(p=>p.id!==source).map(p=><label key={p.id} className="sync-choice"><input type="checkbox" checked={targets.includes(p.id)} onChange={event=>setTargets(current=>event.target.checked?[...current,p.id]:current.filter(id=>id!==p.id))} /><span className="sync-choice-main"><strong>{name(p)}</strong><small>{displaySku(p.sku,t)} · {p.store}</small></span><span className="tiny-part">{displayPart(p.part)}</span></label>)}</div>
        </div><div><div className="sync-section-top"><h3 className="sync-heading">{t('syncMetadata')}</h3><button type="button" className="text small" onClick={()=>setFields(selectedFields.length===fields.length?[]:fields.map(f=>f.id))}>{t(selectedFields.length===fields.length?'syncSelectNone':'selectAll')}</button></div>
          <div className="sync-field-grid">{fields.map(field=>{
            const selected = selectedFields.includes(field.id), sourceValue = value(sourcePackage,field.id)
            const emptyHint = field.id === 'cover' && !sourcePackage.cover && packages.some(p=>targets.includes(p.id) && p.cover) ? 'syncEmptyCoverHint'
              : field.id === 'tags' && !sourcePackage.tags.length && packages.some(p=>targets.includes(p.id) && p.tags.length) ? 'syncEmptyTagsHint' : undefined
            return <label key={field.id} className="sync-field-option"><input type="checkbox" checked={selected} aria-label={t(field.label)} aria-describedby={`sync-value-${field.id}`} onChange={event=>setFields(current=>event.target.checked?[...current,field.id]:current.filter(id=>id!==field.id))} />
              <span className="sync-field-content"><span>{t(field.label)}</span><span id={`sync-value-${field.id}`} className="sync-field-content">
                <span className="sync-source-value" title={sourceValue}>{field.id === 'cover' && sourceCover?.url
                  ? <img className="sync-source-cover" src={sourceCover.url} alt={sourceValue} /> : sourceValue}</span>
                {selected && emptyHint && <small className="sync-field-hint">{t(emptyHint)}</small>}
                {field.id === 'cover' && sourcePackage.cover && sourceCover && !sourceCover.url && <small className="sync-field-hint" title={sourceCover.error ? errorText(sourceCover.error,t) : undefined}>{t('syncCoverPreviewUnavailable')}</small>}
              </span></span></label>
          })}</div>
        </div></div>
        <section className="sync-part-section" aria-label={t('syncNumberParts')}><label className="sync-part-head"><input type="checkbox" checked={renumber} onChange={event=>{cancelDrag();setRenumber(event.target.checked)}} /><strong>{t('syncNumberParts')}</strong><span className="sync-part-scope">{t('syncWholeGroup')}</span></label>
          <div className="sync-part-guide"><span>{t('syncDragHint')}</span><span className="spacer"/><span>{t('syncCurrent')}</span><span aria-hidden="true">→</span><span>{t('syncNew')}</span></div>
          <div className="sync-part-preview" role="list" ref={list}>{order.map((id,index)=>{
            const p=packages.find(p=>p.id===id)!, next=renumber?String(index+1).padStart(2,'0'):displayPart(p.part)
            return <div key={id} data-sync-part={id} role="listitem" tabIndex={renumber&&!busy?0:-1} aria-disabled={!renumber||busy} aria-label={t('syncPartRow',{name:`${name(p)}${id===source?` · ${t('syncSource')}`:''}`})}
              className={`sync-part-row${!renumber?' is-disabled':''}${dragView?.id===id?' part-moving':''}${dragView?.drop?.id===id?(dragView.drop.before?' part-drop-before':' part-drop-after'):''}`}
              onPointerDown={event=>startDrag(event,id)} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={cancelDrag} onLostPointerCapture={cancelDrag}
              onKeyDown={event=>{if(renumber&&!busy&&['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();const target=order[index+(event.key==='ArrowUp'?-1:1)];if(target) reorder(id,{id:target,before:event.key==='ArrowUp'})}}}>
              <span className="sync-part-title"><span className="sync-part-name" title={name(p)}>{name(p)}</span>{id===source && <span className="sync-source-badge">{t('syncSource')}</span>}</span><span className="sync-part-previous">{displayPart(p.part)}</span><span aria-hidden="true">→</span><span className={`sync-part-new${next===displayPart(p.part)?' unchanged':''}`}>{next}</span>
            </div>
          })}</div></section>
      </fieldset> : <>
        <div className="sync-summary-bar"><div><strong>{t('syncSummary',{count:review.changes.length})}</strong><p>{t('syncFieldsCount',{count:review.changes.reduce((sum,c)=>sum+c.fields.length+Number(c.partChanged),0)})}</p></div></div>
        {review.order && <div className="sync-order-note"><Icon name="check" />{t('syncOrderChanged')}</div>}
        <div className="sync-review-list">{review.changes.map(change=>{
          const {before,after} = change
          const cleared = change.fields.filter(field => {
            switch (field) {
              case 'store': return before.store && !after.store || before.prefix && !after.prefix
              case 'tags': return before.tags.length && !after.tags.length
              case 'cover': case 'name': case 'sku': return before[field] && !after[field]
              default: return false
            }
          }).map(field=>t(fields.find(f=>f.id===field)!.label))
          return <article className="sync-review-card" key={after.id}><div className="sync-review-heading"><strong id={`sync-review-${after.id}`}>{name(before)}</strong><small>{t('syncFieldsCount',{count:change.fields.length+Number(change.partChanged)})}</small></div>
            <table className="sync-review-table" aria-labelledby={`sync-review-${after.id}`}>
              <thead><tr><th scope="col">{t('syncField')}</th><th scope="col">{t('syncBefore')}</th><th scope="col">{t('syncAfter')}</th></tr></thead>
              <tbody>{[...change.fields,...(change.partChanged?['part' as const]:[])].map(field=><tr key={field}><th scope="row">{t(field==='part'?'part':fields.find(f=>f.id===field)!.label)}</th><td>{value(before,field)}</td><td className="after">{value(after,field,true)}</td></tr>)}
                {change.oldFilename!==change.newFilename && <tr className="sync-review-file"><th scope="row">{t('syncZipName')}</th><td>{change.oldFilename??t('syncZipUnavailable')}</td><td>{change.newFilename??t('syncZipUnavailable')}</td></tr>}
              </tbody>
            </table>
            {!!cleared.length && <div className="sync-warning sync-clear-warning"><Icon name="warning"/>{t('syncClearsValues',{fields:cleared.join(', ')})}</div>}
          </article>
        })}</div>
        {review.notices.filter(n=>n.issues.length).map(notice=><div className="sync-warning" key={notice.id}><Icon name="warning"/><div>{notice.id && <strong>{name(packages.find(p=>p.id===notice.id)!)}</strong>}{notice.issues.map((issue,i)=><div key={i}>{errorText(issue,t)}</div>)}</div></div>)}
        {review.changes.some(c=>c.oldFilename!==c.newFilename) && <div className="sync-note"><Icon name="info"/>{t('syncZipHint')}</div>}
      </>}
    </div>
  </Modal>
}
