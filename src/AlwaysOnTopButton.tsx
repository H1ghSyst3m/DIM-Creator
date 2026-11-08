import { useEffect, useRef, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { Icon } from './Icon'
import type { Translate } from './messages'

export function AlwaysOnTopButton({ t, closing, onError }: {
  t: Translate; closing: boolean; onError: (error: unknown) => void
}) {
  const [pressed, setPressed] = useState(false)
  const [pending, setPending] = useState(false)
  const toggle = useRef(() => {})

  useEffect(() => {
    if (!isTauri()) return
    let disposed = false
    let busy = false
    const update = async (change = false) => {
      if (disposed || busy) return
      busy = true
      setPending(true)
      try {
        const current = getCurrentWindow()
        const value = await current.isAlwaysOnTop()
        if (disposed) return
        setPressed(value)
        if (change) {
          await current.setAlwaysOnTop(!value)
          if (!disposed) setPressed(!value)
        }
      } catch (error) {
        if (disposed) return
        if (change) {
          try {
            const value = await getCurrentWindow().isAlwaysOnTop()
            if (!disposed) setPressed(value)
          } catch { /* Keep the last confirmed state if the window cannot be queried. */ }
        }
        if (!disposed) onError(error)
      } finally {
        busy = false
        if (!disposed) setPending(false)
      }
    }
    const refresh = () => { void update() }
    toggle.current = () => { void update(true) }
    refresh()
    window.addEventListener('focus', refresh)
    return () => {
      disposed = true
      toggle.current = () => {}
      window.removeEventListener('focus', refresh)
    }
  }, [onError])

  const unavailable = !isTauri() || pending || closing
  const label = t(pressed ? 'disableAlwaysOnTop' : 'enableAlwaysOnTop')
  return <button type="button" className="icon-btn pin-button" title={label} aria-label={label}
    aria-pressed={pressed} aria-disabled={unavailable} aria-busy={pending}
    onClick={() => { if (!unavailable) toggle.current() }}>
    <Icon name={pressed ? 'pin' : 'pinoff'} />
  </button>
}
