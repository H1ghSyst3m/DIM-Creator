import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import { Popup } from './Popup'

type Option = { value: string; label: string; searchText?: string; lang?: string }

export function SingleSelect({ id, label, value, options, onChange, onBlur, disabled = false, invalid, describedBy, searchLabel, emptyText = '—' }: {
  id: string
  label: string
  value: string
  options: Option[]
  onChange: (value: string) => void
  onBlur?: () => void
  disabled?: boolean
  invalid?: boolean
  describedBy?: string
  searchLabel?: string
  emptyText?: string
}) {
  const trigger = useRef<HTMLButtonElement>(null)
  const restoreFocus = useRef(false)
  const search = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const popupId = useId()
  const listId = `${popupId}-list`
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(value)
  const visible = options.filter(option => (option.searchText ?? option.label).toLowerCase().includes(query.trim().toLowerCase()))
  const activeIndex = Math.max(0, visible.findIndex(option => option.value === active))
  const activeId = visible.length ? `${listId}-${activeIndex}` : undefined
  const selected = options.find(option => option.value === value)
  const close = () => setOpen(false)
  const choose = (next: string) => {
    close()
    trigger.current?.focus()
    restoreFocus.current = true
    if (next !== value) onChange(next)
  }
  useEffect(() => { if (disabled) close() }, [disabled])
  useLayoutEffect(() => {
    if (!disabled && restoreFocus.current) {
      restoreFocus.current = false
      if (document.activeElement === document.body) trigger.current?.focus()
    }
  })
  useEffect(() => { close() }, [value])
  useLayoutEffect(() => {
    if (open) (search.current ?? list.current)?.focus()
  }, [open])
  useLayoutEffect(() => {
    if (open && activeId) document.getElementById(activeId)?.scrollIntoView({block:'nearest'})
  }, [open, activeId, query])
  return <div className="single-select" onBlur={event => {
    const next = event.relatedTarget
    if (next instanceof Node && (event.currentTarget.contains(next) || document.getElementById(popupId)?.contains(next))) return
    close()
    onBlur?.()
  }} onKeyDown={event => {
    if (disabled) return
    if (event.key === 'Tab' && open) {
      // Restore the trigger before native Tab navigation leaves the portaled list.
      close()
      trigger.current?.focus()
      return
    }
    if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
      event.preventDefault()
      if (!open) { setQuery(''); setActive(value); setOpen(true); return }
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1 :
        Math.max(0, Math.min(visible.length - 1, activeIndex + (event.key === 'ArrowDown' ? 1 : -1)))
      if (visible[next]) setActive(visible[next].value)
    }
    if (open && (event.key === 'Enter' || event.key === ' ' && event.target !== search.current)) {
      event.preventDefault()
      if (visible[activeIndex]) choose(visible[activeIndex].value)
    }
  }}>
    <button ref={trigger} id={id} type="button" className="single-select-trigger" disabled={disabled}
      aria-label={label} aria-describedby={[`${popupId}-value`,describedBy].filter(Boolean).join(' ')} aria-invalid={invalid}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? popupId : undefined}
      onClick={() => { setQuery(''); setActive(value); setOpen(current => !current) }}>
      <span id={`${popupId}-value`} lang={selected?.lang}>{selected?.label ?? (value || '—')}</span><Icon name="down" />
    </button>
    {open && !disabled && trigger.current && <Popup id={popupId} anchor={trigger.current} alignTo={trigger.current} label={label}
      className="select-popover" autoFocus={false} onClose={reason => { close(); if (reason === 'outside') onBlur?.() }}>
      {searchLabel && <div className="tag-popover-search"><input ref={search} type="search" value={query} aria-label={searchLabel}
        placeholder={searchLabel} role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls={listId}
        aria-activedescendant={activeId} onChange={event => setQuery(event.target.value)} /></div>}
      <div ref={list} id={listId} className="select-options" role="listbox" aria-label={label}
        tabIndex={searchLabel ? -1 : 0} aria-activedescendant={activeId}>
        {visible.map((option,index) => <div key={option.value} id={`${listId}-${index}`} role="option" aria-selected={option.value === value}
          className="select-option" data-active={index === activeIndex} lang={option.lang}
          onPointerMove={() => setActive(option.value)} onPointerDown={event => event.preventDefault()} onClick={() => choose(option.value)}>
          <span>{option.label}</span>{option.value === value && <Icon name="check" />}
        </div>)}
        {!visible.length && <p className="empty">{emptyText}</p>}
      </div>
    </Popup>}
  </div>
}
