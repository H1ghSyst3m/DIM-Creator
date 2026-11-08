import { useEffect, useState } from 'react'
import { Icon } from './Icon'
import { Popup } from './Popup'
import type { Translate } from './messages'

export function TagPicker({ selected, catalog, t, disabled, onChange, onManage }: {
  selected: string[]
  catalog: string[]
  t: Translate
  disabled: boolean
  onChange: (tags: string[]) => void
  onManage: () => void
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [query, setQuery] = useState('')
  useEffect(() => { if (disabled) setAnchor(null) }, [disabled])
  const tags = [...new Set([...catalog, ...selected])].filter(tag => tag.toLowerCase().includes(query.trim().toLowerCase()))
  return <>
    <div className="tag-control">
      <div className="tag-chips">
        {selected.length ? selected.map(tag => <span key={tag} className="tag-chip"><span>{tag}</span>
          <button type="button" aria-label={t('removeTag', { name: tag })} onClick={() => onChange(selected.filter(value => value !== tag))}><Icon name="close" /></button>
        </span>) : <span className="tag-placeholder">{t('noTags')}</span>}
      </div>
      <button id="field-tags" type="button" className="icon-btn tag-toggle" aria-label={t('chooseTags')} aria-haspopup="dialog" aria-expanded={!!anchor}
        onClick={event => { setQuery(''); setAnchor(anchor ? null : event.currentTarget) }}><Icon name="down" /></button>
    </div>
    {anchor && !disabled && <Popup anchor={anchor} alignTo={anchor.closest<HTMLElement>('.tag-control')!} label={t('chooseTags')} className="tag-popover" onClose={() => setAnchor(null)}>
      <div className="tag-popover-search"><input type="search" aria-label={t('tagSearch')} placeholder={t('tagSearch')} value={query} onChange={event => setQuery(event.target.value)} /></div>
      <div className="tag-options">
        {tags.map(tag => <label key={tag} className="tag-choice"><input type="checkbox" checked={selected.includes(tag)}
          onChange={event => onChange(event.target.checked ? [...selected, tag] : selected.filter(value => value !== tag))} /><span>{tag}</span></label>)}
        {!tags.length && <p className="empty">{t('noTagsMatch')}</p>}
      </div>
      <div className="tag-popover-footer"><button className="text" type="button" onClick={() => { setAnchor(null); onManage() }}>{t('manageTags')}</button><span>{t('selected', { count: selected.length })}</span></div>
    </Popup>}
  </>
}
