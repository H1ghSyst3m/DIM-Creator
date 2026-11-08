import { useRef, useState } from 'react'
import { api, type ExportApproval, type ExportResult, type ExportPreflight, type JobEvent } from './api'
import { appError } from './messages'
import type { useWorkspace } from './useWorkspace'

export type ExportDraft = ExportPreflight

export function useExport(workspace: ReturnType<typeof useWorkspace>) {
  const [event, setEvent] = useState<JobEvent | null>(null)
  const [running, setRunning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<unknown>()
  const active = useRef(false)
  const cancelPending = useRef(false)
  const generation = useRef(0)

  const prepare = async (ids: string[], chooseOutput: () => Promise<string | null>): Promise<ExportDraft | null> => {
    if (!ids.length) return null
    await workspace.flush()
    const output = workspace.settings?.outputDir ?? await chooseOutput()
    return output ? api.preflight(ids, output) : null
  }

  const start = async (draft: ExportDraft, ids: string[], replace: ExportApproval[]) => {
    if (active.current) throw appError('exportRunning')
    const current = ++generation.current
    active.current = true
    cancelPending.current = false
    setRunning(true)
    setEvent(null)
    setCancelling(false)
    setCancelError(undefined)
    const confirmed = new Set(ids)
    try {
      const jobId = await api.startExport(draft.context, draft.rows.filter(row => confirmed.has(row.id) && row.outputPath).map(row => ({id:row.id,outputPath:row.outputPath!})), replace, next => {
        if (generation.current !== current || !active.current) return
        if (!next.results) { setEvent(next); return }
        const received = new Map(next.results.map(result => [result.id, result]))
        const results: ExportResult[] = draft.rows.map(row => received.get(row.id) ?? ({
          id: row.id, path: null, status: confirmed.has(row.id) ? 'failed' : 'skipped',
          message: row.issues.find(issue => issue.severity === 'error') ?? (row.existing ? appError('unapproved') : null),
        }))
        active.current = false
        setRunning(false)
        setEvent({ ...next, results })
      })
      setEvent(previous => previous ?? { jobId, packageId: null, phase: 'starting', done: 0, total: 0, results: null })
    } catch (error) {
      active.current = false
      setRunning(false)
      throw error
    }
  }

  const cancel = () => {
    if (!event?.jobId || cancelPending.current || !active.current) return
    const current = generation.current
    cancelPending.current = true
    setCancelling(true)
    setCancelError(undefined)
    void api.cancelExport(event.jobId).catch(error => {
      if (generation.current !== current || !active.current) return
      cancelPending.current = false
      setCancelling(false)
      setCancelError(error)
    })
  }
  return { prepare, start, cancel, event, running, isRunning: () => active.current, cancelling, cancelError }
}
