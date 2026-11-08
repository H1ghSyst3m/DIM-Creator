import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Package, type Preview, type Settings, type FileMutation, type OutputDirectory, type WorklistState, type WorklistAction, type SyncResult, type SyncPreview, type SyncSession } from './api'
import type { ImportResult } from './api'

export function useWorkspace() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [packages, setPackages] = useState<Package[]>([])
  const [worklist, setWorklist] = useState<WorklistState | null>(null)
  const worklistRef = useRef<WorklistState | null>(null)
  const adoptWorklist = useCallback((next: WorklistState, authoritative = false) => {
    if (!authoritative && worklistRef.current && next.revision < worklistRef.current.revision) return
    worklistRef.current = next
    setWorklist(next)
  }, [])
  const [previews, setPreviews] = useState<Record<string, Preview>>({})
  const [outputs, setOutputs] = useState<Record<string, OutputDirectory>>({})
  const [outputErrors, setOutputErrors] = useState<Record<string, unknown>>({})
  const [warnings, setWarnings] = useState<unknown[]>([])
  const [loadError, setLoadError] = useState<unknown>()
  const [saveError, setSaveError] = useState<unknown>()
  const [saving, setSaving] = useState(false)
  const pending = useRef(new Map<string, Package>())
  const inFlight = useRef<Promise<void> | null>(null)
  const settingsWrite = useRef<Promise<void> | null>(null)
  const deferPreview = useRef(false)
  const deferredPreviews = useRef(new Set<string>())
  const timer = useRef<number | undefined>(undefined)
  const revisions = useRef(new Map<string, number>())
  const outputRevisions = useRef(new Map<string, number>())

  const invalidateOutputs = useCallback((ids: string[]) => {
    const versions = new Map(ids.map(id => [id, (outputRevisions.current.get(id) ?? 0) + 1]))
    versions.forEach((version, id) => outputRevisions.current.set(id, version))
    setOutputs(current => Object.fromEntries(Object.entries(current).filter(([id]) => !versions.has(id))))
    setOutputErrors(current => Object.fromEntries(Object.entries(current).filter(([id]) => !versions.has(id))))
    return versions
  }, [])

  const refreshOutputs = useCallback(async (ids: string[]) => {
    if (!ids.length) return
    const versions = invalidateOutputs(ids)
    try {
      const targets = await api.previewOutputs(ids)
      setOutputs(current => ({...current, ...Object.fromEntries(targets
        .filter(target => outputRevisions.current.get(target.id) === versions.get(target.id) && !pending.current.has(target.id))
        .map(target => [target.id, target]))}))
    } catch (error) {
      setOutputErrors(current => ({...current, ...Object.fromEntries([...versions]
        .filter(([id, version]) => outputRevisions.current.get(id) === version).map(([id]) => [id, error]))}))
    }
  }, [invalidateOutputs])

  const refresh = useCallback(async (id: string) => {
    const revision = (revisions.current.get(id) ?? 0) + 1
    revisions.current.set(id, revision)
    const [preview] = await Promise.all([api.preview(id), refreshOutputs([id])])
    if (revisions.current.get(id) === revision && !pending.current.has(id)) {
      deferredPreviews.current.delete(id)
      setPreviews(current => ({ ...current, [id]: preview }))
    }
  }, [refreshOutputs])

  const load = useCallback(async () => {
    setLoadError(undefined)
    try {
      const data = await api.loadWorkspace()
      setPackages(data.packages)
      adoptWorklist(data.worklist)
      setSettings(data.settings)
      setWarnings(data.warnings)
      void (async () => {
        for (const item of data.packages) {
          await refresh(item.id).catch(error => setWarnings(current => [...current, error]))
        }
      })()
    } catch (error) {
      setLoadError(error)
    }
  }, [refresh, adoptWorklist])

  const flushPackages = useCallback(async () => {
    window.clearTimeout(timer.current)
    if (inFlight.current) {
      await inFlight.current
      return
    }
    if (!pending.current.size) return

    setSaving(true)
    setSaveError(undefined)
    const save = async () => {
      const saved = new Set<string>()
      while (pending.current.size) {
        for (const item of pending.current.values()) {
          await api.savePackage(item)
          if (pending.current.get(item.id) === item) pending.current.delete(item.id)
          saved.add(item.id)
        }
      }
      for (const id of saved) {
        if (deferPreview.current) {
          deferredPreviews.current.add(id)
          revisions.current.set(id, (revisions.current.get(id) ?? 0) + 1)
          setPreviews(current => current[id] ? {...current, [id]: {...current[id], stale:true}} : current)
          continue
        }
        void refresh(id).catch(error => setWarnings(current => [...current, error]))
      }
    }
    inFlight.current = save()
    try {
      await inFlight.current
    } catch (error) {
      setSaveError(error)
      throw error
    } finally {
      inFlight.current = null
      setSaving(false)
    }
  }, [refresh])

  const flush = useCallback(async () => {
    await flushPackages()
    if (worklistRef.current?.saveError) adoptWorklist(await api.retryWorklist())
  }, [flushPackages, adoptWorklist])

  const edit = useCallback((item: Package) => {
    pending.current.set(item.id, item)
    invalidateOutputs([item.id])
    setPackages(current => current.map(entry => entry.id === item.id ? item : entry))
    setSaving(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void flush().catch(() => undefined), 220)
  }, [flush, invalidateOutputs])

  useEffect(() => {
    void load()
  }, [load])

  const flushForClose = useCallback(async () => {
    await settingsWrite.current
    await flush()
  }, [flush])

  const prepareMetadataAction = async () => {
    deferPreview.current = true
    try { await settingsWrite.current; await flush() }
    finally { deferPreview.current = false }
  }

  const adoptImport = (result: ImportResult) => {
    if(result.worklist) adoptWorklist(result.worklist,true)
    if(!result.packages.length)return
    const ids=result.packages.map(item=>item.id)
    for(const id of ids){revisions.current.set(id,(revisions.current.get(id)??0)+1);deferredPreviews.current.add(id)}
    invalidateOutputs(ids)
    setPackages(current=>{
      const next=new Map(current.map(item=>[item.id,item]))
      for(const item of result.packages)next.set(item.id,item)
      return [...next.values()]
    })
    setPreviews(current=>{
      const next={...current}
      for(const id of ids){if(next[id])next[id]={...next[id],stale:true}}
      return next
    })
  }

  const adoptSyncSession = (session: SyncSession) => {
    const changed = session.data.packages.filter(item => deferredPreviews.current.has(item.id) || !previews[item.id] || previews[item.id].stale ||
      JSON.stringify(packages.find(current => current.id === item.id)) !== JSON.stringify(item)).map(item => item.id)
    for (const id of changed) revisions.current.set(id,(revisions.current.get(id) ?? 0)+1)
    invalidateOutputs(changed)
    for (const id of changed) deferredPreviews.current.delete(id)
    setPreviews(current => ({...current,...Object.fromEntries(session.metadata.filter(row=>changed.includes(row.id)).map(row=>[
      row.id,{...row.preview,entries:current[row.id]?.entries ?? [],size:current[row.id]?.size ?? 0,stale:true},
    ]))}))
    setOutputs(current => ({...current,...Object.fromEntries(session.outputs.filter(row=>changed.includes(row.id)).map(row=>[row.id,row]))}))
    adoptWorklist(session.worklist, true)
    setPackages(current => {
      const next = new Map(current.map(item => [item.id,item]))
      for (const item of session.data.packages) next.set(item.id,item)
      return [...next.values()]
    })
  }

  const adoptSync = (result: SyncResult, preview: SyncPreview) => {
    const ids = [...result.packages.map(item => item.id), ...result.unavailable.map(item => item.id)]
    for (const id of ids) {
      revisions.current.set(id, (revisions.current.get(id) ?? 0) + 1)
      pending.current.delete(id)
      deferredPreviews.current.delete(id)
    }
    invalidateOutputs(ids)
    adoptWorklist(result.worklist, true)
    setPackages(current => current.flatMap(item => {
      if (!ids.includes(item.id)) return [item]
      const actual = result.packages.find(next => next.id === item.id)
      return actual ? [actual] : []
    }))
    setPreviews(current => {
      const next = {...current}
      for (const id of ids) {
        if (result.error) { delete next[id]; continue }
        const metadata = preview.metadata.find(row => row.id === id)?.preview
        if (metadata) {
          const stale = !current[id] || current[id].stale || preview.changes.some(change => change.after.id === id)
          next[id] = stale ? {...metadata, entries:current[id]?.entries ?? [], size:current[id]?.size ?? 0, stale:true} : {...current[id],filename:metadata.filename}
        }
      }
      return next
    })
    if (!result.error) setOutputs(current => ({...current, ...Object.fromEntries(preview.outputs.map(output => [output.id,output]))}))
    if (result.unavailable.length) setWarnings(current => [...current, ...result.unavailable.map(item => item.error)])
  }

  const add = async (name: string) => {
    await flush()
    const result = await api.createPackage(name)
    const item = result.package
    adoptWorklist(result.worklist)
    setPackages(current => [...current, item])
    void refresh(item.id).catch(error => setWarnings(current => [...current, error]))
    return item
  }

  const remove = async (id: string) => {
    await flushPackages()
    adoptWorklist(await api.removePackage(id))
    revisions.current.delete(id)
    deferredPreviews.current.delete(id)
    invalidateOutputs([id])
    setPackages(current => current.filter(item => item.id !== id))
    setPreviews(current => {
      const next = { ...current }
      delete next[id]
      return next
    })
  }

  const changeWorklist = async (action?: WorklistAction) => {
    await flushPackages()
    const current = worklistRef.current
    if (!current) return false
    const next = action ? await api.changeWorklist(current.revision, action) : await api.undoWorklist(current.revision)
    adoptWorklist(next)
    return next.revision !== current.revision
  }

  const setCover = async (id: string, source: string | null) => {
    await flush()
    const item = await api.setCover(id, source)
    setPackages(current => current.map(entry => entry.id === id ? item : entry))
    await refresh(id)
  }

  const changeSettings = async (next: Settings) => {
    const write = (async () => {
      await flush()
      const saved = await api.saveSettings(next)
      setSettings(saved)
      if (saved.outputDir !== settings?.outputDir || saved.outputOrganization !== settings?.outputOrganization) {
        void refreshOutputs(packages.map(item => item.id))
      }
    })()
    settingsWrite.current = write
    try { await write }
    finally { if (settingsWrite.current === write) settingsWrite.current = null }
    if (JSON.stringify(next.dazFolders) !== JSON.stringify(settings?.dazFolders)) {
      for (const item of packages) revisions.current.set(item.id, (revisions.current.get(item.id) ?? 0) + 1)
      setPreviews({})
      for (const item of packages) await refresh(item.id).catch(error => setWarnings(current => [...current, error]))
    }
  }

  const applyFiles = (result: FileMutation) => {
    const item = result.package
    revisions.current.set(item.id, (revisions.current.get(item.id) ?? 0) + 1)
    setPackages(current => current.map(entry => entry.id === item.id ? item : entry))
    if (result.saveError) {
      pending.current.set(item.id, item)
      setSaveError(result.saveError)
      setSaving(false)
    }
  }

  return {
    settings, packages: worklist ? worklist.entries.flatMap(entry => entry.kind === 'package' ? [entry.id] : entry.members)
      .flatMap(id => { const item = packages.find(item => item.id === id); return item ? [item] : [] }) : packages,
    worklist, changeWorklist, previews, outputs, warnings: [...warnings, ...(worklist?.warnings ?? []), ...Object.values(outputErrors)], loadError, saveError: saveError ?? worklist?.saveError, saving,
    prepareMetadataAction, adoptImport, adoptWorklist, adoptSyncSession, adoptSync, load, refresh, refreshOutputs, edit, flush, flushForClose, settingsSaving: () => settingsWrite.current !== null, add, remove, setCover, changeSettings, applyFiles,
  }
}
