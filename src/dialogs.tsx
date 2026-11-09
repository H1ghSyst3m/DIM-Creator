import { ThemeTiles } from './ThemePicker'
import { useValidation } from './useValidation'
import { ClosingContext } from './closeRequest'
import { SingleSelect } from './SingleSelect'
import { useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type ExportApproval, type ExportResult, type ExportRow, type FileEntry, type JobEvent, type Package, type Preview, type Settings, type Update, type SettingsIssue } from './api'
import { languages, resolveLanguage, errorText, isMessage, sizeText, type Message, type Translate } from './messages'
import { issueText } from './messages'
import { displayPart, displaySku, matchesStore, reviewedExport, validExportIds } from './workflow'
import { Icon, type IconName } from './Icon'
import { Logo } from './Logo'

const repository = 'https://github.com/H1ghSyst3m/DIM-Creator'

export function Modal({ title, subtitle, t, children, footer, onClose, wide, className = '' }: {
  title: string
  subtitle?: string
  t: Translate
  children: ReactNode
  footer?: ReactNode
  onClose?: () => void
  wide?: boolean
  className?: string
}) {
  const shutdown = useContext(ClosingContext)
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current
    const priorFocus = document.activeElement
    dialog?.showModal()
    dialog?.querySelector<HTMLElement>('[data-initial-focus]')?.focus()
    return () => {
      dialog?.close()
      if (priorFocus instanceof HTMLElement && priorFocus.isConnected) priorFocus.focus()
    }
  }, [])
  return <dialog ref={ref} className={`${wide ? 'wide' : ''} ${className}`} aria-labelledby={titleId} inert={shutdown.closing} aria-busy={shutdown.closing}
    onCancel={event => { event.preventDefault(); if (!shutdown.closing) onClose?.() }}>
    <div className="dialog-head">
      <div><h2 id={titleId}>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
      {onClose && <button type="button" className="icon-btn" onClick={onClose} aria-label={t('close')}><Icon name="close" /></button>}
    </div>
    <div className="dialog-body">{shutdown.error != null && <p className="error" role="alert">{errorText(shutdown.error,t)}</p>}{children}</div>
    {footer && <div className="dialog-footer">{footer}</div>}
  </dialog>
}

export function NameDialog({ type, folder, t, onClose, onConfirm }: {
  type: 'package' | 'folder'
  folder?: string
  t: Translate
  onClose: () => void
  onConfirm: (name: string) => Promise<void>
}) {
  const [value, setValue] = useState('')
  const [error, setError] = useState<unknown>()
  const [busy, setBusy] = useState(false)
  const id = useId()
  const validation = useValidation(value, api.validateEntryName, type === 'folder' && value.length > 0)
  const nameError = type === 'folder' ? validation.result ?? validation.error : undefined
  return <Modal title={t(type === 'package' ? 'addPackage' : 'newFolder')} subtitle={type === 'folder' ? t('folderLocation', { path: folder ? '/' + folder : '' }) : undefined} t={t} onClose={busy ? undefined : onClose}
    footer={<>
      <button type="button" onClick={onClose} disabled={busy}>{t('cancel')}</button>
      <button form={id} type="submit" className="primary" disabled={busy || !value.trim()}>{t('add')}</button>
    </>}>
    <form id={id} onSubmit={event => {
      event.preventDefault()
      if (busy || !value.trim()) return
      setBusy(true)
      setError(undefined)
      void (async () => {
        try {
          if (type === 'folder') {
            const failure = await validation.check()
            if (failure) { setError(failure); return }
          }
          await onConfirm(type === 'folder' ? value : value.trim())
        } catch (error) { setError(error) }
        finally { setBusy(false) }
      })()
    }}>
      <label className="field">{t(type === 'package' ? 'name' : 'folderName')}
        <input data-initial-focus value={value} readOnly={busy} maxLength={type === 'package' ? 160 : 255} onChange={event => { setValue(event.target.value); setError(undefined) }} required />
      </label>
      {(error != null || nameError != null) && <p className="error" role="alert">{errorText(error ?? nameError, t)}</p>}
    </form>
  </Modal>
}

export type SettingsTab = 'general' | 'stores' | 'tags' | 'dazFolders' | 'updatesAbout'

export function SettingsDialog({ settings, initialTab, t, update, updateStatus, onCheck, onClose, onSave, onPreviewTheme }: {
  settings: Settings
  initialTab: SettingsTab
  t: Translate
  update: Update | null
  updateStatus: string
  onCheck: () => void
  onClose: () => void
  onSave: (settings: Settings) => Promise<void>
  onPreviewTheme?: (theme: string) => void
}) {
  const [editor, setEditor] = useState(() => {
    const {defaultStore, defaultTags, ...values} = settings
    return {values, store:values.stores.findIndex(store => store.name === defaultStore),
      tags:values.tags.flatMap((tag,index) => defaultTags.includes(tag) ? [index] : [])}
  })
  // Row positions keep selections stable while names are incomplete or temporarily duplicated.
  const draft = useMemo<Settings>(() => ({...editor.values,
    defaultStore:editor.values.stores[editor.store]?.name ?? '',
    defaultTags:editor.values.tags.filter((_,index) => editor.tags.includes(index)),
  }), [editor])
  const [tab, setTab] = useState(initialTab)
  const [query, setQuery] = useState('')
  const [storeQuery, setStoreQuery] = useState('')
  const [editingStore, setEditingStore] = useState<number | null>(null)
  const [editingTag, setEditingTag] = useState<number | null>(null)
  const [error, setError] = useState<unknown>()
  const [busy, setBusy] = useState(false)
  const formId = useId()
  const pageTitle = useId()
  const addedRow = useRef<HTMLInputElement>(null)
  const focusNew = useRef(false)
  const pendingFocus = useRef<string | null>(null)
  const [validated, setValidated] = useState(false)
  const validation = useValidation(draft, api.validateSettings)
  const problems = (validation.result ?? []).filter(problem => validated || problem.severity === 'warning')
  const cellId = (category: string, index: number, field: string) => `${formId}-${category}-${index}-${field}`
  const cellIssue = (category: string, index: number, field: string) => problems.find(problem => problem.tab === category && problem.index === index && problem.field === field)
  const cellAttrs = (category: string, index: number, field: string) => {
    const problem = cellIssue(category,index,field)
    return { id:cellId(category,index,field), 'aria-invalid':problem?.severity === 'error', 'data-warning':problem?.severity === 'warning', 'aria-describedby':problem ? cellId(category,index,field)+'-note' : undefined }
  }
  const cellNote = (category: string, index: number, field: string) => {
    const problem = cellIssue(category,index,field)
    return problem && <div className={`settings-cell-note ${problem.severity}`} id={cellId(category,index,field)+'-note'}><Icon name={problem.severity === 'error' ? 'error' : 'warning'} /><span>{errorText(problem, t)}</span></div>
  }
  const focusIssue = (issue: SettingsIssue) => {
    pendingFocus.current = cellId(issue.tab,issue.index,issue.field)
    setTab(issue.tab as SettingsTab)
    setQuery('')
    setStoreQuery('')
  }
  useLayoutEffect(() => {
    if (pendingFocus.current && !busy) {
      document.getElementById(pendingFocus.current)?.focus()
      pendingFocus.current = null
    }
  })
  const change = (changes: Partial<typeof editor.values>) => setEditor(current => ({...current,values:{...current.values,...changes}}))
  const openLink = (url: string) => void api.openUrl(url).catch(setError)
  const icons: Record<SettingsTab, IconName> = { general: 'settings', stores: 'archive', tags: 'list', dazFolders: 'folder', updatesAbout: 'info' }
  useEffect(() => {
    if (focusNew.current) { addedRow.current?.focus(); focusNew.current = false }
  }, [draft.stores.length, draft.tags.length, draft.dazFolders.length])
  return <Modal title={t('settings')} t={t} onClose={busy ? undefined : onClose} className="settings-dialog" footer={<>
    {(error != null || validation.error != null || problems.some(problem => problem.severity === 'error')) && <span className="settings-footer-note error" role="alert">{error != null || validation.error != null ? errorText(error ?? validation.error, t) : t('checkInputs')}</span>}
    <button type="button" disabled={busy} onClick={onClose}>{t('cancel')}</button>
    <button type="submit" form={formId} disabled={busy} className="primary">{t('save')}</button>
  </>}>
    <div className="settings-layout">
      <nav className="settings-nav" aria-label={t('settings')}>
        {(['general', 'stores', 'tags', 'dazFolders', 'updatesAbout'] as const).map(key => <button key={key} type="button" aria-current={tab === key ? 'page' : undefined} onClick={() => setTab(key)}><Icon name={icons[key]} /><span>{t(key)}</span></button>)}
      </nav>
      <form className={`settings-page settings-${tab}`} id={formId} onSubmit={event => {
        event.preventDefault()
        if (busy) return
        setError(undefined)
        setValidated(true)
        setBusy(true)
        void (async () => {
          try {
            const firstError = (await validation.check()).find(problem => problem.severity === 'error')
            if (firstError) { focusIssue(firstError); return }
            await onSave(draft)
          } catch (error) { setError(error) }
          finally { setBusy(false) }
        })()
      }}>
        <fieldset disabled={busy} aria-labelledby={pageTitle}>
          <div className="settings-page-title"><h3 id={pageTitle}>{t(tab)}</h3>
            {(tab === 'stores' || tab === 'tags' || tab === 'dazFolders') && <>
              <small>{t('catalogEntries', {count: draft[tab].length})}</small>
              <button type="button" className="small" onClick={() => {
                focusNew.current = true
                if (tab === 'stores') { setStoreQuery(''); change({stores:[...draft.stores,{name:'',prefix:''}]}) }
                else { setQuery(''); if (tab === 'tags' || tab === 'dazFolders') change({[tab]:[...draft[tab],'']}) }
              }}><Icon name="plus" />{t(tab === 'stores' ? 'addStore' : tab === 'tags' ? 'addTag' : 'addDazFolder')}</button>
            </>}
          </div>
          {tab === 'general' && <>
            <div className="setting-row"><label htmlFor={`${formId}-language`}>{t('language')}</label><SingleSelect id={`${formId}-language`} label={t('language')} value={resolveLanguage(draft.language).id} disabled={busy}
              options={languages.map(language => ({value:language.id,label:language.name,lang:language.locale}))} onChange={language => change({language})} /></div>
            <ThemeTiles value={draft.theme} disabled={busy} t={t} onChange={theme => { change({theme}); onPreviewTheme?.(theme) }}/>
            <div className="setting-template-folder">
              <label htmlFor={cellId('general',0,'templateDir')}>{t('templateDirectory')}</label>
              <div className="setting-path"><input {...cellAttrs('general',0,'templateDir')} aria-label={t('templateDirectory')} value={draft.templateDir??''} placeholder={t('templateDirectoryEmpty')}
                onChange={event=>change({templateDir:event.target.value||null})}/>
                <button type="button" className="icon-btn" title={t('templateChooseDirectory')} aria-label={t('templateChooseDirectory')} onClick={()=>void(async()=>{
                  setBusy(true);setError(undefined)
                  try{const path=await api.chooseOutput(t('templateDirectory'));if(typeof path==='string')change({templateDir:path})}
                  catch(error){setError(error)}finally{setBusy(false)}
                })()}><Icon name="folder"/></button>
              </div>{cellNote('general',0,'templateDir')}
              <label className="check"><input type="checkbox" checked={draft.autoSaveTemplates} onChange={event=>change({autoSaveTemplates:event.target.checked})}/>{t('templateAutomatic')}</label>
              <p className="muted">{t('templateAutomaticHint')}</p>
            </div>
          </>}
          {tab === 'stores' && <>
            <input className="settings-search" type="search" value={storeQuery} onChange={event => setStoreQuery(event.target.value)} placeholder={t('storeSearch')} aria-label={t('storeSearch')} />
            <div className="settings-table-scroll"><table className="settings-table store-settings-table">
              <thead><tr><th>#</th><th>{t('store')}</th><th>{t('prefix')}</th><th className="default-column">{t('defaultSelection')}</th><th><span className="sr-only">{t('remove')}</span></th></tr></thead>
              <tbody>{draft.stores.map((store, index) => (matchesStore(store,storeQuery) || editingStore === index) && <tr key={index}
                onFocus={() => setEditingStore(index)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setEditingStore(null) }}><td>{String(index+1).padStart(2,'0')}</td>
                <td><input {...cellAttrs('stores',index,'name')} ref={index === draft.stores.length - 1 ? addedRow : undefined} aria-label={`${t('store')} ${index + 1}`} value={store.name} maxLength={80} autoComplete="off"
                  onChange={event => change({ stores: draft.stores.map((entry, row) => row === index ? { ...entry, name: event.target.value } : entry) })} />{cellNote('stores',index,'name')}</td>
                <td><input {...cellAttrs('stores',index,'prefix')} className="prefix-input" aria-label={`${t('prefix')} ${index + 1}`} value={store.prefix} maxLength={7} spellCheck={false} autoComplete="off"
                  onChange={event => change({ stores: draft.stores.map((entry, row) => row === index ? { ...entry, prefix: event.target.value.toUpperCase() } : entry) })} />{cellNote('stores',index,'prefix')}</td>
                <td className="default-column"><input {...cellAttrs('stores',index,'default')} type="radio" name={`${formId}-default-store`}
                  checked={editor.store === index} aria-label={t('defaultStoreChoice',{name:store.name || t('store')})}
                  onChange={() => setEditor(current => ({...current,store:index}))} />{cellNote('stores',index,'default')}</td>
                <td><button type="button" className="icon-btn danger" aria-label={`${store.name || t('store')} ${t('remove')}`}
                  disabled={editor.store === index} title={editor.store === index ? t('defaultStoreRemoval') : undefined}
                  // Keep a filtered edit row in place until the remove click has completed.
                  onPointerDown={event => event.preventDefault()}
                  onClick={() => { setEditingStore(null); setEditor(current => ({...current,
                    values:{...current.values,stores:current.values.stores.filter((_,row) => row !== index)},
                    store:current.store > index ? current.store - 1 : current.store,
                  })) }}><Icon name="trash" /></button></td>
              </tr>)}</tbody>
            </table>{!draft.stores.some((store,index) => matchesStore(store,storeQuery) || editingStore === index) && <p className="empty">{t(draft.stores.length ? 'noStoresMatch' : 'noStores')}</p>}</div>
            <p className="settings-hint">{t('catalogHelp')}</p>
            <p className="settings-hint">{t('defaultStoreRemoval')}</p>
          </>}
          {(tab === 'tags' || tab === 'dazFolders') && <>
            <input className="settings-search" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t(tab === 'tags' ? 'tagSearch' : 'dazFolderSearch')} aria-label={t(tab === 'tags' ? 'tagSearch' : 'dazFolderSearch')} />
            <div className="settings-table-scroll"><table className="settings-table">
              <thead><tr><th>#</th><th>{t(tab)}</th>{tab === 'tags' && <th className="default-column">{t('defaultSelection')}</th>}<th><span className="sr-only">{t('remove')}</span></th></tr></thead>
              <tbody>{draft[tab].map((value, index) => (!query.trim() || value.toLowerCase().includes(query.trim().toLowerCase()) || tab === 'tags' && editingTag === index) && <tr key={index}
                onFocus={() => { if (tab === 'tags') setEditingTag(index) }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setEditingTag(null) }}><td>{String(index+1).padStart(2,'0')}</td>
                <td><input {...cellAttrs(tab,index,tab === 'tags' ? 'tag' : 'folder')} ref={index === draft[tab].length - 1 ? addedRow : undefined} aria-label={`${t(tab)} ${index + 1}`} value={value} maxLength={tab === 'tags' ? 80 : 255} autoComplete="off" onChange={event => change({[tab]:draft[tab].map((entry,row) => row === index ? event.target.value : entry)})} />{cellNote(tab,index,tab === 'tags' ? 'tag' : 'folder')}</td>
                {tab === 'tags' && <td className="default-column"><input {...cellAttrs('tags',index,'default')} type="checkbox"
                  checked={editor.tags.includes(index)} aria-label={t('defaultTagChoice',{name:value || t('tags')})}
                  onChange={event => { const checked=event.target.checked; setEditor(current => ({...current,
                    tags:checked ? [...current.tags,index] : current.tags.filter(row => row !== index),
                  })) }} />{cellNote('tags',index,'default')}</td>}
                <td><button type="button" className="icon-btn danger" aria-label={t(tab === 'tags' ? 'removeTag' : 'removeDazFolder',{name:value})}
                  onPointerDown={event => event.preventDefault()}
                  onClick={() => { setEditingTag(null); setEditor(current => ({...current,
                    values:{...current.values,[tab]:current.values[tab].filter((_,row) => row !== index)},
                    tags:tab === 'tags' ? current.tags.filter(row => row !== index).map(row => row > index ? row - 1 : row) : current.tags,
                  })) }}><Icon name="trash" /></button></td>
              </tr>)}</tbody>
            </table>{!draft[tab].some((value,index) => value.toLowerCase().includes(query.trim().toLowerCase()) || tab === 'tags' && editingTag === index) && <p className="empty">{t(tab === 'tags' ? 'noTagsMatch' : 'noDazFoldersMatch')}</p>}</div>
            <p className="settings-hint">{t(tab === 'tags' ? 'catalogHelp' : 'dazFoldersHelp')}</p>
            {tab === 'dazFolders' && !draft.dazFolders.length && <p className="warning"><Icon name="warning" /> {t('dazFoldersEmpty')}</p>}
          </>}
          {tab === 'updatesAbout' && <>
            <div className="settings-about"><Logo /><div><strong>DIM Creator</strong><p>{t('version', { version: __APP_VERSION__ })}</p></div></div>
            <label className="settings-update-option"><input type="checkbox" checked={draft.checkUpdates} onChange={event => change({ checkUpdates: event.target.checked })} />{t('checkOnLaunch')}</label>
            <div><button type="button" onClick={onCheck} disabled={updateStatus === t('updateChecking')}><Icon name="refresh" />{t('checkUpdates')}</button></div>
            <p className="settings-update-status" role="status">{updateStatus}</p>
            {update && <button type="button" className="primary" onClick={() => openLink(update.url)}>{t('releases')}</button>}
            <div className="link-row">
              <button className="text" type="button" onClick={() => openLink(repository)}>{t('project')}<Icon name="external" /></button>
              <button className="text" type="button" onClick={() => openLink(repository + '/releases')}>{t('releases')}<Icon name="external" /></button>
              <button className="text" type="button" onClick={() => openLink(repository + '/blob/main/CHANGELOG.md')}>{t('changelog')}<Icon name="external" /></button>
            </div>
          </>}
        </fieldset>
      </form>
    </div>
  </Modal>
}

export function ValidationDialog({ item, preview, t, onClose, onJump }: {
  item: Package
  preview?: Preview
  t: Translate
  onClose: () => void
  onJump: (field: string) => void
}) {
  const errors = preview?.issues.filter(issue => issue.severity === 'error').length ?? 0
  const warnings = preview?.issues.filter(issue => issue.severity === 'warning').length ?? 0
  const summary = !preview ? t('checking') : [errors ? t('errors',{count:errors}) : '', warnings ? t('warnings',{count:warnings}) : ''].filter(Boolean).join(' · ') || t('noIssues')
  const rows = [
    {key:'name',label:'name',value:item.name}, {key:'store',label:'store',value:item.store},
    {key:'prefix',label:'prefix',value:item.prefix}, {key:'sku',label:'sku',value:item.sku.trim() ? item.sku : t('skuMissing')},
    {key:'part',label:'part',value:displayPart(item.part)}, {key:'guid',label:'guid',value:item.guid},
    {key:'tags',label:'tags',value:item.tags.join(', '),optional:!item.tags.length},
    {key:'cover',label:'cover',value:item.cover ? t('cover') : t('noCover'),optional:!item.cover},
    {key:'content',label:'content',value:t('contentFiles',{count:preview?.entries.filter(entry => entry.startsWith('Content/') && !entry.endsWith('/')).length ?? 0})},
    {key:'support',label:'support',value:t(item.cleanSupport ? 'cleanSupportHelp' : 'keepSupportHelp')},
    {key:'output',label:'outputFile',value:preview?.filename ?? t('incomplete')},
  ] satisfies {key:string;label:Message;value:string;optional?:boolean}[]
  return <Modal title={t('validation')} subtitle={`${item.name || t('unnamed')} · ${displaySku(item.sku, t)} · Part ${displayPart(item.part)}`}
    t={t} onClose={onClose} className="validation-dialog" footer={<><span className="validation-scope">{t('validationScope')}</span><button type="button" onClick={onClose}>{t('close')}</button></>}>
    <div className={`validation-summary ${errors ? 'error' : warnings ? 'warning' : 'success'}`} role="status"><Icon name={errors ? 'error' : warnings ? 'warning' : preview ? 'check' : 'clock'} /><strong>{summary}</strong></div>
    <table className="validation-table" aria-label={t('checkResults')}>
      <colgroup><col className="check-label-col" /><col /><col className="check-result-col" /><col className="check-action-col" /></colgroup>
      <thead className="sr-only"><tr><th>{t('properties')}</th><th>{t('checkValue')}</th><th>{t('checkState')}</th><th>{t('open')}</th></tr></thead>
      <tbody>{rows.map(row => {
        const issues = preview?.issues.filter(issue => issue.field === row.key) ?? []
        const state = !preview ? 'pending' : issues.some(issue => issue.severity === 'error') ? 'error' : issues.length ? 'warning' : row.optional ? 'neutral' : row.key === 'output' && !preview.filename ? 'pending' : 'success'
        const status: Message = state === 'error' ? 'checkError' : state === 'warning' ? 'checkWarning' : state === 'neutral' ? 'optional' : state === 'pending' ? 'checkPending' : 'checkOk'
        return <tr key={row.key} className={`validation-row ${state} ${row.key === 'content' ? 'section-start' : ''}`}>
          <th scope="row">{t(row.label)}</th><td className={`check-value ${['prefix','sku','part','guid','output'].includes(row.key) && !issues.length ? 'mono' : ''}`}>{issues.length ? issues.map((issue,index) => <p key={index}>{issueText(issue,t)}</p>) : row.value || '—'}</td>
          <td><span className={`check-result ${state}`}><Icon name={state === 'error' ? 'error' : state === 'warning' ? 'warning' : state === 'success' ? 'check' : state === 'pending' ? 'clock' : 'info'} /><span>{t(status)}</span></span></td>
          <td><button type="button" className="icon-btn check-jump" aria-label={t('checkField',{field:t(row.label)})} title={t('checkField',{field:t(row.label)})} onClick={() => onJump(row.key)}><Icon name="arrow" /></button></td>
        </tr>
      })}</tbody>
    </table>
  </Modal>
}

export function DetailsDialog({ entry, t, language, onClose }: { entry: FileEntry; t: Translate; language: string; onClose: () => void }) {
  return <Modal title={t('detail')} t={t} onClose={onClose} footer={<button type="button" onClick={onClose}>{t('close')}</button>}>
    <dl className="file-details">
      <dt>{t('fileName')}</dt><dd>{entry.name}</dd>
      <dt>{t('type')}</dt><dd>{entry.directory ? t('folder') : entry.kind}</dd>
      <dt>{t('size')}</dt><dd>{entry.directory ? '—' : sizeText(entry.size, language)}</dd>
    </dl>
    <p className="path muted">Content/{entry.path}</p>
  </Modal>
}

export function ExportReview({ rows, t, onClose, onStart }: {
  rows: ExportRow[]
  t: Translate
  onClose: () => void
  onStart: (ids: string[], replace: ExportApproval[]) => void
}) {
  const [approved, setApproved] = useState<ReadonlySet<string>>(new Set())
  const validIds = new Set(validExportIds(rows))
  const { ids, replace } = reviewedExport(rows, approved)
  const commonOutput = rows[0]?.outputDir && rows.every(row => row.outputDir === rows[0].outputDir) ? rows[0].outputDir : null
  return <Modal title={t('exportTitle')} t={t} onClose={onClose} wide footer={<>
    <button type="button" onClick={onClose}>{t('cancel')}</button>
    <button type="button" className="primary" disabled={!ids.length}
      onClick={() => onStart(ids, replace)}>{t('start', { count: ids.length })}</button>
  </>}>
    <p className="muted">{t('exportReview')}</p>{commonOutput && <p className="path">{commonOutput}</p>}
    <ul className="issue-list">{rows.map(row => <li key={row.id}>
      <div><strong>{row.name || t('unnamed')}</strong><p className="mono">{row.filename ?? t('incomplete')}</p>
        {!commonOutput && row.outputDir && <p className="path">{row.outputDir}</p>}
        {row.issues.map((issue, index) => <p key={index} className={issue.severity}>{issueText(issue, t)}</p>)}
        {row.existing && <p className="warning">{t('existing')}{!approved.has(row.id) && ` · ${t('unapproved')}`}</p>}
      </div>
      {row.existing && row.stamp && validIds.has(row.id) && <label className="toggle replace-zip">
        <input type="checkbox" checked={approved.has(row.id)} onChange={event => {
          const next = new Set(approved)
          if (event.target.checked) next.add(row.id)
          else next.delete(row.id)
          setApproved(next)
        }} /> {t('confirmReplace')}
      </label>}
    </li>)}</ul>
  </Modal>
}

const resultMessages = {
  success: 'resultSuccess', failed: 'resultFailed', skipped: 'resultSkipped', cancelled: 'resultCancelled',
} as const satisfies Record<ExportResult['status'], Message>

export function ExportProgress({ rows, event, results, cancelling, error, t, onClose, onCancel }: {
  rows: ExportRow[]
  event: JobEvent | null
  results: ExportResult[] | null
  cancelling: boolean
  error: unknown
  t: Translate
  onClose: () => void
  onCancel: () => void
}) {
  const [revealing, setRevealing] = useState(false)
  const [revealError, setRevealError] = useState<{ id: string; error: unknown }>()
  const reveal = async (id: string, path: string) => {
    setRevealing(true)
    setRevealError(undefined)
    try { await api.revealExport(path) }
    catch (error) { setRevealError({ id, error }) }
    finally { setRevealing(false) }
  }
  const name = (id: string | null | undefined) => rows.find(row => row.id === id)?.name || t('unnamed')
  return <Modal title={t(results ? 'resultsTitle' : 'progressTitle')} t={t} onClose={results ? onClose : undefined} wide footer={
    results ? <button type="button" onClick={onClose}>{t('close')}</button> :
      <button type="button" disabled={cancelling || !event?.jobId} onClick={onCancel}>{t(cancelling ? 'cancelling' : 'cancel')}</button>
  }>
    {results ? <ul className="issue-list">{results.map(result => <li key={result.id}>
      <div><strong>{name(result.id)}</strong>
        {result.path && <p className="mono">{result.path}</p>}
        {result.message && <p className={result.status === 'failed' ? 'error' : 'muted'}>{errorText(result.message, t)}</p>}
        {result.status === 'success' && result.path && <p><button type="button" className="ghost small" disabled={revealing}
          aria-label={t('showPackageInExplorer', { name: name(result.id) })} onClick={() => void reveal(result.id, result.path!)}>
          <Icon name="external" />{t('showInExplorer')}
        </button></p>}
        {revealError?.id === result.id && <p className="error" role="alert">{errorText(revealError.error, t)}</p>}
      </div>
      <span className={result.status === 'success' ? 'success' : result.status === 'failed' ? 'error' : 'muted'}>{t(resultMessages[result.status])}</span>
    </li>)}</ul> : <>
      {event?.packageId && <p><strong>{name(event.packageId)}</strong></p>}
      <p role="status">{event && isMessage(event.phase) ? t(event.phase) : t('preparing')}</p>
      <progress max={event?.total || 1} value={event?.total ? Math.min(event.done, event.total) : undefined} aria-label={t('progressTitle')} />
      {error != null && <p className="error" role="alert">{errorText(error, t)}</p>}
    </>}
  </Modal>
}
