import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Translate } from './messages'

export function panelGeometry(preferred: number | null, available: number, standard: number) {
  const max = Math.max(300, available - 420)
  return { width: Math.min(max, Math.max(300, preferred ?? standard)), max }
}

export function PanelDivider({ width, disabled, t, onChange }: {
  width: number | null
  disabled: boolean
  t: Translate
  onChange: (width: number | null) => Promise<void>
}) {
  const ref = useRef<HTMLDivElement>(null)
  const restoreFocus = useRef(false)
  const [preferred, setPreferred] = useState(width)
  const preferredRef = useRef(width)
  const [dimensions, setDimensions] = useState({ available: 720, standard: 350, desktop: false })
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{ id: number; x: number; width: number; previous: number | null } | null>(null)
  const keyboard = useRef<{ previous: number | null } | null>(null)
  const geometry = panelGeometry(preferred, dimensions.available, dimensions.standard)
  const setWidth = (value: number | null) => { preferredRef.current = value; setPreferred(value) }
  const cancel = () => {
    if (drag.current) setWidth(drag.current.previous)
    if (keyboard.current) setWidth(keyboard.current.previous)
    drag.current = null
    keyboard.current = null
    setDragging(false)
  }
  useEffect(() => { setWidth(width) }, [width])
  useEffect(() => { if (disabled) cancel() }, [disabled])
  useLayoutEffect(() => {
    if (!disabled && restoreFocus.current) {
      restoreFocus.current = false
      if (document.activeElement === document.body) ref.current?.focus()
    }
  })

  useLayoutEffect(() => {
    const main = ref.current!.parentElement!
    const list = main.querySelector<HTMLElement>('.worklist')!
    const measure = () => {
      const desktop = window.innerWidth > 1050
      if (!desktop) cancel()
      setDimensions({ desktop, available: main.clientWidth - list.getBoundingClientRect().width - 6,
        standard: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--inspector-width')) })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(main)
    observer.observe(list)
    window.addEventListener('resize', measure)
    return () => { observer.disconnect(); window.removeEventListener('resize', measure) }
  }, [])

  useLayoutEffect(() => {
    const main = ref.current!.parentElement!
    main.style.setProperty('--panel-inspector-width', `${geometry.width}px`)
    main.classList.toggle('pane-resizing', dragging)
    return () => { main.style.removeProperty('--panel-inspector-width'); main.classList.remove('pane-resizing') }
  }, [geometry.width, dragging])

  const commit = (value: number | null, previous: number | null) => {
    if (value !== previous) {
      restoreFocus.current = document.activeElement === ref.current
      void onChange(value)
    }
  }
  const reset = () => {
    const previous = preferredRef.current
    drag.current = null
    keyboard.current = null
    setDragging(false)
    setWidth(null)
    commit(null, previous)
  }
  const finishKeyboard = () => {
    const current = keyboard.current
    keyboard.current = null
    if (current) commit(preferredRef.current, current.previous)
  }
  return <div ref={ref} className="panel-divider" role="separator" aria-orientation="vertical"
    aria-label={t('panelDivider')} aria-controls="package-properties" aria-valuemin={300} aria-valuemax={geometry.max}
    aria-valuenow={geometry.width} aria-valuetext={t('panelWidth',{width:geometry.width})}
    aria-disabled={disabled} tabIndex={dimensions.desktop && !disabled ? 0 : -1} title={t('panelDividerHelp')}
    onPointerDown={event => {
      if (disabled || event.button !== 0 || !dimensions.desktop) return
      event.preventDefault()
      event.currentTarget.focus()
      drag.current = {id:event.pointerId,x:event.clientX,width:geometry.width,previous:preferredRef.current}
      event.currentTarget.setPointerCapture(event.pointerId)
      setDragging(true)
    }}
    onPointerMove={event => {
      const current = drag.current
      if (!current || current.id !== event.pointerId) return
      setWidth(panelGeometry(Math.round(current.width + event.clientX-current.x), dimensions.available, dimensions.standard).width)
    }}
    onPointerUp={event => {
      const current = drag.current
      if (!current || current.id !== event.pointerId) return
      drag.current = null
      setDragging(false)
      event.currentTarget.releasePointerCapture(event.pointerId)
      commit(preferredRef.current, current.previous)
    }}
    onPointerCancel={cancel} onLostPointerCapture={() => { if (drag.current) cancel() }}
    onDoubleClick={() => { if (!disabled) reset() }}
    onKeyDown={event => {
      if (disabled) return
      if (event.key === 'Escape') { event.preventDefault(); cancel() }
      if (event.key === 'Enter') { event.preventDefault(); reset() }
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        keyboard.current ??= {previous:preferredRef.current}
        const current = panelGeometry(preferredRef.current, dimensions.available, dimensions.standard).width
        const step = (event.shiftKey ? 50 : 10) * (event.key === 'ArrowLeft' ? -1 : 1)
        setWidth(panelGeometry(current + step, dimensions.available, dimensions.standard).width)
      }
    }}
    onKeyUp={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') finishKeyboard() }}
    onBlur={() => { if (drag.current) cancel(); else finishKeyboard() }} />
}
