import { createContext } from 'react'
import type { CloseRequestedEvent } from '@tauri-apps/api/window'

export const ClosingContext = createContext<{ closing: boolean; error: unknown }>({closing:false,error:undefined})

export function registerCloseHandler(
  subscribe: (handler: (event: CloseRequestedEvent) => Promise<void>) => Promise<() => void>,
  options: {
    busy: () => boolean
    flush: () => Promise<void>
    canClose: () => Promise<boolean>
    onClosing: (closing: boolean) => void
    onError: (error: unknown) => void
  },
) {
  let disposed = false
  let closing = false
  let unlisten: (() => void) | undefined
  void subscribe(async event => {
    if (disposed || closing) { event.preventDefault(); return }
    if (options.busy()) { event.preventDefault(); options.onError('closeBusy'); return }
    closing = true
    options.onClosing(true)
    try {
      await options.flush()
      if (disposed) { event.preventDefault(); return }
      if (!await options.canClose()) throw new Error('closeBusy')
      if (disposed) event.preventDefault()
      // Tauri completes this original request after the asynchronous handler returns.
    } catch (error) {
      event.preventDefault()
      closing = false
      if (!disposed) { options.onClosing(false); options.onError(error) }
    }
  }).then(listener => {
    if (disposed) listener()
    else unlisten = listener
  }).catch(() => { if (!disposed) options.onError('closeHandlerFailed') })
  return () => { disposed = true; unlisten?.() }
}
