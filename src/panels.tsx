import { SingleSelect } from './SingleSelect'
import type { Package, Preview, Settings } from './api'
import { issueText, type Translate } from './messages'
import { displayPart } from './workflow'
import { Icon } from './Icon'
import { TagPicker } from './TagPicker'

type InspectorProps = {
  item?: Package
  settings: Settings
  preview?: Preview
  cover: string | null
  t: Translate
  disabled: boolean
  shown: ReadonlySet<string>
  reveal: (field: string) => void
  change: (changes: Partial<Package>) => void
  chooseCover: () => void
  removeCover: () => void
  generateGuid: () => void
  manageTags: () => void
  manageStores: () => void
  synchronize?: () => void
  build: () => void
  remove: () => void
}

export function Inspector(props: InspectorProps) {
  const { t, item, settings, disabled } = props
  const feedback = (key: string) => {
    const issues = props.preview?.issues.filter(issue => issue.field === key) ?? []
    if (!props.shown.has(key) && !props.shown.has('*')) return null
    return issues.length ? <div id={`issue-${key}`} className="inline-issues">
      {issues.map(issue => <p key={issue.code} className={issue.severity}><Icon name={issue.severity === 'error' ? 'error' : 'warning'} />{issueText(issue, t)}</p>)}
    </div> : null
  }
  const invalid = (key: string) => (props.shown.has(key) || props.shown.has('*')) &&
    (props.preview?.issues.some(issue => issue.field === key && issue.severity === 'error') ?? false)
  const field = (name: 'name' | 'prefix' | 'sku' | 'part' | 'guid', options?: { max?: number; numeric?: boolean; disabled?: boolean }) =>
    <input id={`field-${name}`} value={item?.[name] ?? ''} maxLength={options?.max}
      inputMode={options?.numeric ? 'numeric' : undefined} disabled={options?.disabled} spellCheck={name === 'name'}
      aria-invalid={invalid(name)} aria-describedby={feedback(name) ? `issue-${name}` : undefined}
      onChange={event => props.change({ [name]: name === 'prefix' ? event.target.value.toUpperCase() : event.target.value })}
      onBlur={() => props.reveal(name)} />
  return <aside className="panel inspector" id="package-properties" aria-label={t('properties')}>
    <div className="panel-title"><h2>{t('properties')}</h2>{item && <div className="inspector-head-actions">{props.synchronize && <button type="button" className="inspector-sync-btn" disabled={disabled} onClick={props.synchronize}><Icon name="refresh" />{t('sync')}</button>}<small>Part {displayPart(item.part)}</small></div>}</div>
    {!item ? <p className="empty">{t('openPackage')}</p> : <>
      <div className="panel-scroll inspector-body">
        <fieldset disabled={disabled}>
          <div className="field">
            <label htmlFor="field-name">{t('name')}</label>
            {field('name', { max: 160 })}{feedback('name')}
          </div>
          <div className="field-grid">
            <div className="field">
              <div className="field-heading"><label htmlFor="field-store">{t('store')}</label><button type="button" className="icon-btn manage-values" onClick={props.manageStores} aria-label={t('manageStores')} title={t('manageStores')}><Icon name="settings" /></button></div>
              <SingleSelect key={item.id} id="field-store" label={t('store')} value={item.store} disabled={disabled}
                invalid={invalid('store')} describedBy={feedback('store') ? 'issue-store' : undefined}
                searchLabel={t('storeSearch')} emptyText={t('noStoresMatch')} onBlur={() => props.reveal('store')}
                options={[
                  ...(!settings.stores.some(entry => entry.name === item.store) ? [{value:item.store,label:item.store || '—'}] : []),
                  ...settings.stores.map(entry => ({value:entry.name,label:entry.name,searchText:`${entry.name} ${entry.prefix}`})),
                ]}
                onChange={store => {
                  const prefix = settings.stores.find(entry => entry.name === store)?.prefix ?? ''
                  props.change(item.autoPrefix ? { store, prefix } : { store })
                }} />
              {feedback('store')}
            </div>
            <div className="field">
              <div className="field-heading"><label htmlFor="field-prefix">{t('prefix')}</label>
                <label className="toggle"><input type="checkbox" checked={item.autoPrefix}
                  disabled={!item.autoPrefix && !settings.stores.some(entry => entry.name === item.store)}
                  onChange={event => props.change(event.target.checked ? {
                    autoPrefix: true, prefix: settings.stores.find(entry => entry.name === item.store)?.prefix ?? '',
                  } : { autoPrefix: false })} /> {t('autoPrefix')}</label>
              </div>
              {field('prefix', { max: 7, disabled: item.autoPrefix })}{feedback('prefix')}
            </div>
          </div>
          <div className="field-grid identifier-grid">
            <div className="field"><label htmlFor="field-sku">{t('sku')}</label>{field('sku', { max: 8, numeric: true })}{feedback('sku')}</div>
            <div className="field"><label htmlFor="field-part">{t('part')}</label>{field('part', { max: 2, numeric: true })}{feedback('part')}</div>
          </div>
          <div className="field">
            <div className="field-heading"><label htmlFor="field-guid">{t('guid')}</label><button type="button" className="text small" onClick={props.generateGuid}>{t('generateGuid')}</button></div>
            {field('guid', { max: 36 })}{feedback('guid')}
          </div>
          <div className="field">
            <div className="field-heading"><span>{t('tags')} <span className="optional-label">{t('optional')}</span></span><button type="button" className="icon-btn manage-values" aria-label={t('manageTags')} title={t('manageTags')} onClick={props.manageTags}><Icon name="settings" /></button></div>
            <TagPicker selected={item.tags} catalog={settings.tags} t={t} disabled={disabled} onChange={tags => props.change({ tags })} onManage={props.manageTags} />
            {feedback('tags')}
          </div>
          <div className="field section" data-drop-target="cover">
            <div className="field-heading"><span>{t('cover')} <span className="optional-label">{t('optional')}</span></span>
              {item.cover && <button type="button" className="text small" onClick={props.removeCover}>{t('remove')}</button>}
            </div>
            <div className="cover-row">
              <button className="cover-preview" id="field-cover" type="button" onClick={props.chooseCover} aria-label={t('chooseCover')}>
                {props.cover ? <img src={props.cover} alt={t('cover')} /> : <><Icon name="image" />{t('noCover')}</>}
              </button>
              <div><button type="button" className="small" onClick={props.chooseCover}><Icon name="image" />{t('chooseCover')}</button><small className="cover-formats">JPG, PNG, WebP</small></div>
            </div>
            {feedback('cover')}
          </div>
          <div className="field section option-block">
            <h3>{t('options')}</h3>
            <label className="toggle"><input id="field-support" type="checkbox" checked={item.cleanSupport} onChange={event => props.change({ cleanSupport: event.target.checked })} /> {t('cleanSupport')}</label>
            <small>{t(item.cleanSupport ? 'cleanSupportHelp' : 'keepSupportHelp')}</small>
            {feedback('support')}
          </div>
        </fieldset>
      </div>
      <div className="panel-footer inspector-footer">
        <button type="button" className="text small" disabled={disabled} onClick={props.build}>{t('buildOne')}</button>
        <button type="button" className="icon-btn danger" title={t('removePackage')} aria-label={t('removePackage')} disabled={disabled} onClick={props.remove}><Icon name="trash" /></button>
      </div>
    </>}
  </aside>
}
