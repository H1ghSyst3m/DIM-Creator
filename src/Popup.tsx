import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export type PopupAnchor = HTMLElement | { x: number; y: number; returnFocus: HTMLElement }

export function Popup({ anchor, alignTo, label, menu = false, className = '', children, onClose, id, autoFocus = true }: {
  anchor: PopupAnchor
  alignTo?: HTMLElement
  label: string
  menu?: boolean
  className?: string
  children: ReactNode
  onClose: (reason: 'outside' | 'escape' | 'blur' | 'layout') => void
  id?: string
  autoFocus?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ left: 0, top: 0 })
  const close = useRef(onClose)
  close.current = onClose
  const returnFocus = anchor instanceof HTMLElement ? anchor : anchor.returnFocus
  const container = returnFocus.closest('dialog') ?? document.body

  useLayoutEffect(() => {
    const panel = ref.current!
    const place = () => {
      const rect = (alignTo ?? (anchor instanceof HTMLElement ? anchor : null))?.getBoundingClientRect()
      const bounds = container instanceof HTMLDialogElement ? container.getBoundingClientRect() : {left:0,top:0,right:window.innerWidth,bottom:window.innerHeight}
      panel.style.maxHeight = `${bounds.bottom - bounds.top - 16}px`
      panel.style.maxWidth = `${bounds.right - bounds.left - 16}px`
      if (alignTo && rect) panel.style.width = `${rect.width}px`
      const x = rect?.left ?? (anchor as { x: number }).x
      const y = rect?.bottom ?? (anchor as { y: number }).y
      const top = y + 4 + panel.offsetHeight <= bounds.bottom - 8 ? y + 4 : Math.max(bounds.top + 8, (rect?.top ?? y) - panel.offsetHeight - 4)
      const left = Math.max(bounds.left + 8, Math.min(x, bounds.right - panel.offsetWidth - 8))
      setPosition(current => current.left === left && current.top === top ? current : {left,top})
    }
    const onScroll = (event: Event) => {
      if (event.target instanceof Node && panel.contains(event.target)) return
      if (menu) close.current('layout')
      else place()
    }
    place()
    if (autoFocus) panel.querySelector<HTMLElement>('input, button:not(:disabled)')?.focus()
    const observer = new ResizeObserver(place)
    observer.observe(panel)
    if (alignTo) observer.observe(alignTo)
    document.addEventListener('scroll', onScroll, true)
    return () => { observer.disconnect(); document.removeEventListener('scroll', onScroll, true) }
  }, [anchor, alignTo, menu, container, autoFocus])

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !ref.current?.contains(event.target) && !returnFocus.contains(event.target)) close.current('outside')
    }
    const resize = () => close.current('layout')
    document.addEventListener('pointerdown', dismiss)
    window.addEventListener('resize', resize)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('resize', resize)
    }
  }, [returnFocus])

  return createPortal(<div id={id} ref={ref} className={`popup ${menu ? 'context-menu' : ''} ${className}`}
    role={menu ? 'menu' : 'dialog'} aria-label={label} style={position}
    onBlur={event => {
      if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== returnFocus) close.current('blur')
    }}
    onKeyDown={event => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close.current('escape')
        returnFocus.focus()
      }
      if (!menu) return
      const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]:not(:disabled)')]
      const index = items.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'ArrowDown' ? (index + 1) % items.length : event.key === 'ArrowUp' ? (index - 1 + items.length) % items.length : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : -1
      if (next >= 0) { event.preventDefault(); items[next]?.focus() }
    }}>{children}</div>, container)
}
