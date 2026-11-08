import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type Package, type Settings } from './api'
import { useWorkspace } from './useWorkspace'

const item: Package = {id:'test',name:'Test',store:'LOCAL USER',prefix:'LOCAL',autoPrefix:true,sku:'1',part:'01',guid:'guid',tags:[],cover:null,cleanSupport:true,excluded:[]}
const settings: Settings = {language:'de',theme:'dark',stores:[{name:'LOCAL USER',prefix:'LOCAL'}],tags:[],defaultStore:'LOCAL USER',defaultTags:[],dazFolders:[],checkUpdates:false,outputDir:null,inspectorWidth:null,outputOrganization:'flat',templateDir:null,autoSaveTemplates:false}
const deferred = () => {
  let resolve!: () => void
  let reject!: (error: unknown) => void
  const promise = new Promise<void>((done,fail) => { resolve = done; reject = fail })
  return {promise,resolve,reject}
}

function workspace() {
  let value!: ReturnType<typeof useWorkspace>
  // Exercise the real save refs/callbacks without mounting startup effects or a desktop window.
  function Probe() { value = useWorkspace(); return null }
  renderToStaticMarkup(<Probe />)
  return value
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('window', {setTimeout,clearTimeout})
  vi.spyOn(api,'savePackage').mockResolvedValue()
  vi.spyOn(api,'saveSettings').mockImplementation(async settings => settings)
  vi.spyOn(api,'previewOutputs').mockResolvedValue([])
  vi.spyOn(api,'preview').mockResolvedValue({filename:null,issues:[],entries:[],size:0})
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('flushes a debounced edit before closing and clears its timer', async () => {
  const state = workspace()
  state.edit(item)
  await vi.advanceTimersByTimeAsync(219)
  expect(api.savePackage).not.toHaveBeenCalled()
  await state.flushForClose()
  await vi.runAllTimersAsync()
  expect(api.savePackage).toHaveBeenCalledExactlyOnceWith(item)
})

it('flushes synchronization edits without starting content previews, including an in-flight save', async () => {
  const state = workspace(), write = deferred()
  vi.mocked(api.savePackage).mockReturnValueOnce(write.promise)
  state.edit(item)
  await vi.advanceTimersByTimeAsync(220)
  const preparing = state.prepareMetadataAction()
  write.resolve()
  await preparing
  expect(api.preview).not.toHaveBeenCalled()
  state.edit({...item, name:'Unsaved draft'})
  await state.prepareMetadataAction()
  expect(api.savePackage).toHaveBeenLastCalledWith({...item, name:'Unsaved draft'})
  expect(api.preview).not.toHaveBeenCalled()
})

it('waits for an in-flight save and drains edits queued during it', async () => {
  const state = workspace(), write = deferred()
  vi.mocked(api.savePackage).mockReturnValueOnce(write.promise)
  state.edit(item)
  await vi.advanceTimersByTimeAsync(220)
  state.edit({...item,sku:'2'})
  let closed = false
  const closing = state.flushForClose().then(() => { closed = true })
  await Promise.resolve()
  expect(closed).toBe(false)
  write.resolve()
  await closing
  expect(api.savePackage).toHaveBeenCalledTimes(2)
  expect(api.savePackage).toHaveBeenLastCalledWith({...item,sku:'2'})
})

it('retains failed package edits for a later save attempt', async () => {
  const state = workspace()
  vi.mocked(api.savePackage).mockRejectedValueOnce('disk failure')
  state.edit(item)
  await expect(state.flushForClose()).rejects.toBe('disk failure')
  await state.flushForClose()
  expect(api.savePackage).toHaveBeenCalledTimes(2)
  expect(api.savePackage).toHaveBeenLastCalledWith(item)
})

it('awaits an already submitted settings write before closing', async () => {
  const state = workspace(), write = deferred()
  vi.mocked(api.saveSettings).mockReturnValueOnce(write.promise.then(() => settings))
  const saving = state.changeSettings(settings)
  let closed = false
  const closing = state.flushForClose().then(() => { closed = true })
  await Promise.resolve()
  expect(state.settingsSaving()).toBe(true)
  expect(closed).toBe(false)
  write.resolve()
  await Promise.all([saving,closing])
  expect(state.settingsSaving()).toBe(false)
  expect(api.saveSettings).toHaveBeenCalledExactlyOnceWith(settings)
})

it('propagates a concurrent settings failure to both Save and Close', async () => {
  const state = workspace(), write = deferred()
  vi.mocked(api.saveSettings).mockReturnValueOnce(write.promise.then(() => settings))
  const saving = state.changeSettings(settings)
  const closing = state.flushForClose()
  write.reject('settings failure')
  await expect(saving).rejects.toBe('settings failure')
  await expect(closing).rejects.toBe('settings failure')
  expect(state.settingsSaving()).toBe(false)
  await state.changeSettings(settings)
  await state.flushForClose()
})
