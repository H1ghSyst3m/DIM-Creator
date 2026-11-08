import { useEffect, useId, useRef, useState } from 'react'
import { Icon } from './Icon'
import { Popup } from './Popup'
import { resolveTheme, themeGroups, themeLabel } from './themes'
import type { Translate } from './messages'

function ThemeSwatch({id}: {id: string}) {
  return <span className="theme-swatch" data-theme={id} data-theme-category={resolveTheme(id).category} aria-hidden="true"><i/><i/><i/></span>
}

export function ThemeMenu({value, disabled, t, onChange}: {
  value: string; disabled: boolean; t: Translate; onChange: (id: string) => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const menuId = useId()
  const current = resolveTheme(value)
  const groups = themeGroups()
  useEffect(() => { if (disabled) setOpen(false) }, [disabled])
  const choose = async (id: string) => {
    setOpen(false)
    trigger.current?.focus()
    if (id !== current.id) await onChange(id)
    // Saving briefly disables the trigger. Restore keyboard focus after React commits.
    requestAnimationFrame(() => {
      if (document.activeElement === document.body) trigger.current?.focus()
    })
  }
  return <>
    <button ref={trigger} type="button" className="icon-btn" disabled={disabled}
      title={`${t('chooseTheme')}: ${themeLabel(current,t)}`} aria-label={`${t('chooseTheme')}: ${themeLabel(current,t)}`}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
      onClick={() => setOpen(value => !value)} onKeyDown={event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true) }
      }}><Icon name="appearance" /></button>
    {open && !disabled && trigger.current && <Popup id={menuId} anchor={trigger.current} label={t('theme')} menu className="theme-menu" onClose={() => setOpen(false)}>
      {groups.map(({category,schemes}) => <div key={category} onKeyDown={event => {
        if (event.key === 'Tab') { setOpen(false); trigger.current?.focus() }
      }}>
        {groups.length > 1 && <h3 className="theme-category-label">{t(category)}</h3>}
        {schemes.map(group => <div key={group.colorScheme} role="group" aria-label={groups.length > 1 ? `${t(category)} · ${t(group.colorScheme)}` : t(group.colorScheme)}>
          <p className="theme-group-label">{t(group.colorScheme)}</p>
          {group.themes.map(theme => <button type="button" key={theme.id} data-theme-id={theme.id} role="menuitemradio"
            aria-checked={current.id === theme.id} aria-label={themeLabel(theme,t)} onClick={() => void choose(theme.id)}>
            <ThemeSwatch id={theme.id}/><span>{t(theme.label)}</span>{current.id === theme.id && <Icon name="check"/>}
          </button>)}
        </div>)}
      </div>)}
    </Popup>}
  </>
}

export function ThemeTiles({value, disabled, t, onChange}: {
  value: string; disabled: boolean; t: Translate; onChange: (id: string) => void
}) {
  const name = useId()
  const groups = themeGroups()
  const current = resolveTheme(value).id
  return <section className="theme-picker" aria-label={t('theme')}>
    <h4>{t('theme')}</h4>
    {groups.map(({category,schemes}) => <div key={category}>
      {groups.length > 1 && <h4 className="theme-category-label">{t(category)}</h4>}
      {schemes.map(group => <div key={group.colorScheme} role="group" aria-label={groups.length > 1 ? `${t(category)} · ${t(group.colorScheme)}` : t(group.colorScheme)}>
        <p className="theme-group-label">{t(group.colorScheme)}</p>
        <div className="theme-tiles">{group.themes.map(theme => <label key={theme.id} data-theme-id={theme.id} className="theme-tile" data-selected={current === theme.id}>
          <ThemeSwatch id={theme.id}/><span>{t(theme.label)}</span>
          <input type="radio" name={name} value={theme.id} checked={current === theme.id} disabled={disabled}
            aria-label={themeLabel(theme,t)} onChange={() => onChange(theme.id)}/>
        </label>)}</div>
      </div>)}
    </div>)}
  </section>
}
