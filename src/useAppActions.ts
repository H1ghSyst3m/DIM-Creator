import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { api } from './api'
import { registerCloseHandler } from './closeRequest'
import { appError } from './messages'
import type { useWorkspace } from './useWorkspace'

export function useAppActions(workspace: ReturnType<typeof useWorkspace>, isExporting: () => boolean) {
  const operation = useRef(false)
  const closingRef = useRef(false)
  const [busy, setBusy] = useState(false)
  const [closing, setClosing] = useState(false)
  const [error, setError] = useState<unknown>()
  const [closeError, setCloseError] = useState<unknown>()
  const closeBusy = useEffectEvent(() => isExporting() || (operation.current && !workspace.settingsSaving()))
  const flush = useEffectEvent(() => workspace.flushForClose())
  useEffect(() => {
    if (!isTauri()) return
    return registerCloseHandler(handler => getCurrentWindow().onCloseRequested(handler), {
      busy: () => closeBusy(), flush: () => flush(), canClose: api.canClose,
      onClosing: value => { closingRef.current = value; setClosing(value); if (value) setCloseError(undefined) },
      onError: setCloseError,
    })
  }, [])

  const available = () => !operation.current && !closingRef.current && !isExporting()
  const perform = async <T,>(work: () => Promise<T>): Promise<T> => {
    if (!available()) throw appError('closeBusy')
    operation.current = true
    setBusy(true)
    try { return await work() }
    finally { operation.current = false; setBusy(false) }
  }
  const run = async <T,>(work: () => Promise<T>): Promise<T | undefined> => {
    if (!available()) return
    setError(undefined)
    try { return await perform(work) }
    catch (error) { setError(error) }
  }
  return { busy, closing, closeError, setCloseError, error, setError, available, perform, run }
}
