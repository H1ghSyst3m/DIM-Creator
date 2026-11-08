// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { act, useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type Settings } from './api'
import { NameDialog, SettingsDialog } from './dialogs'
import { appError, translator } from './messages'
import { click, deferred, input, mount, testSettings } from './testSupport'
import { SingleSelect } from './SingleSelect'
import { outputOrganizationOptions } from './outputOrganizations'
import { themes } from './themes'

let dispose: (() => void) | undefined
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
  HTMLElement.prototype.scrollIntoView = () => {}
  vi.spyOn(api,'validateSettings').mockResolvedValue([])
})
afterEach(() => { dispose?.(); dispose=undefined; vi.restoreAllMocks(); vi.unstubAllGlobals() })

const button = (label: string) => [...document.querySelectorAll('button')].find(button => button.textContent === label)!

for (const theme of themes.filter(theme => theme.category === 'glass')) it(`preserves the reserved-prefix warning border in ${theme.id}`, async () => {
  vi.useFakeTimers()
  document.documentElement.dataset.theme = theme.id
  document.documentElement.dataset.themeCategory = theme.category
  try {
    vi.mocked(api.validateSettings).mockResolvedValue([{...appError('reservedPrefix'),tab:'stores',index:0,field:'prefix',severity:'warning'}])
    const settings = {...testSettings,theme:theme.id,stores:[{name:'My Store',prefix:'IM'}],defaultStore:'My Store'}
    dispose = (await mount(<SettingsDialog settings={settings} initialTab="stores" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={()=>{}} onSave={async()=>{}} />)).unmount
    await act(async () => { await vi.advanceTimersByTimeAsync(150) })
    const prefix = document.querySelector<HTMLInputElement>('input[aria-label="Prefix 1"]')!
    expect(prefix.value).toBe('IM')
    expect(prefix.dataset.warning).toBe('true')
    expect(prefix.getAttribute('aria-invalid')).toBe('false')
    expect(document.getElementById(prefix.getAttribute('aria-describedby')!)?.textContent).toContain('Prefix reserved for DAZ.')
    // Reserved prefixes need the semantic warning border, not the normal Glass border.
    const glassMaterial = readFileSync('src/themes/glass/material.css', 'utf8')
    const warningSelector = glassMaterial.match(/([^{}]+)\{\s*border-color: var\(--warning\);/)?.[1].trim()
    expect(warningSelector).toBeDefined()
    expect(prefix.matches(warningSelector!)).toBe(true)
    expect(document.querySelector('input[aria-label="Store 1"]')!.matches(warningSelector!)).toBe(false)
  } finally {
    delete document.documentElement.dataset.theme
    delete document.documentElement.dataset.themeCategory
    vi.useRealTimers()
  }
})

for (const theme of themes.slice(2)) it(`saves and restores ${theme.id}, but discards a cancelled theme draft`, async () => {
  const onSave = vi.fn<(settings: Settings) => Promise<void>>().mockResolvedValue(), onClose = vi.fn(), t = translator('en')
  const props = {settings:testSettings,initialTab:'general' as const,t,update:null,updateStatus:'',onCheck:()=>{},onClose,onSave}
  let view = await mount(<SettingsDialog {...props} />); dispose=view.unmount
  expect(document.querySelectorAll('.theme-tile')).toHaveLength(8)
  await click(document.querySelector(`[data-theme-id="${theme.id}"] input`)!)
  expect(onSave).not.toHaveBeenCalled()
  await click(button('Save'))
  expect(onSave).toHaveBeenCalledWith({...testSettings,theme:theme.id})
  view.unmount()

  view = await mount(<SettingsDialog {...props} settings={onSave.mock.calls[0][0]} />); dispose=view.unmount
  expect(document.querySelector<HTMLInputElement>(`[data-theme-id="${theme.id}"] input`)?.checked).toBe(true)
  await click(document.querySelector('[data-theme-id="dark"] input')!)
  expect(document.querySelector<HTMLInputElement>('[data-theme-id="dark"] input')?.checked).toBe(true)
  await click(button('Cancel'))
  expect(onSave).toHaveBeenCalledOnce()
  expect(onClose).toHaveBeenCalled()
  expect(testSettings.theme).toBe('dark')
})

const catalogSettings = {
  ...testSettings,
  stores: [{name:'First',prefix:'F'}, {name:'Second',prefix:'S'}, {name:'Third',prefix:'T'}],
  tags: ['General','DAZStudio4_5','Blender'],
  defaultStore: 'Second', defaultTags: ['DAZStudio4_5'],
}

it('keeps the template directory and automatic selection in the settings draft until save',async()=>{
  const onSave=vi.fn(async()=>{}),onClose=vi.fn()
  const props={settings:testSettings,initialTab:'general' as const,t:translator('en'),update:null,updateStatus:'',onCheck:()=>{},onClose,onSave}
  const view=await mount(<SettingsDialog {...props}/>);dispose=view.unmount
  const directory=view.host.querySelector<HTMLInputElement>('input[aria-label="Template folder"]')
  expect(directory).not.toBeNull()
  await input(directory!,'D:\\DAZ Templates')
  await click(view.host.querySelector('input[type="checkbox"]')!)
  expect(onSave).not.toHaveBeenCalled()
  await click(button('Save'))
  expect(onSave).toHaveBeenCalledWith(expect.objectContaining({templateDir:'D:\\DAZ Templates',autoSaveTemplates:true}))
})

it('keeps the default store on its row during filtering, renaming and removal of preceding rows', async () => {
  const onSave = vi.fn(async () => {})
  dispose = (await mount(<SettingsDialog settings={catalogSettings} initialTab="stores" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={()=>{}} onSave={onSave} />)).unmount
  const radios = () => [...document.querySelectorAll<HTMLInputElement>('input[type=radio]')]
  expect(radios().map(radio=>radio.checked)).toEqual([false,true,false])
  expect(document.querySelector<HTMLButtonElement>('button[aria-label="Second Remove"]')!.disabled).toBe(true)
  await input(document.querySelector('input[aria-label="Store 2"]')!, 'First')
  expect(radios().map(radio=>radio.checked)).toEqual([false,true,false])
  await input(document.querySelector('input[aria-label="Store 2"]')!, '')
  expect(radios().map(radio=>radio.checked)).toEqual([false,true,false])
  await input(document.querySelector('input[aria-label="Store 2"]')!, 'Renamed')
  await input(document.querySelector('input[type=search]')!, 'First')
  expect(radios().map(radio=>radio.checked)).toEqual([false])
  await click(document.querySelector('button[aria-label="First Remove"]')!)
  await input(document.querySelector('input[type=search]')!, '')
  expect(radios().map(radio=>radio.checked)).toEqual([true,false])
  await click(radios()[1])
  await click(button('Save'))
  expect(onSave).toHaveBeenCalledWith({...catalogSettings,stores:[{name:'Renamed',prefix:'S'},{name:'Third',prefix:'T'}],defaultStore:'Third'})
  expect(catalogSettings.defaultStore).toBe('Second')
})

it('maintains default tags across filtered edits, duplicate names and removals, including no defaults', async () => {
  const onSave = vi.fn(async () => {})
  dispose = (await mount(<SettingsDialog settings={catalogSettings} initialTab="tags" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={()=>{}} onSave={onSave} />)).unmount
  const checks = () => [...document.querySelectorAll<HTMLInputElement>('input[type=checkbox]')]
  expect(checks().map(check=>check.checked)).toEqual([false,true,false])
  await click(checks()[2])
  await input(document.querySelector('input[type=search]')!, 'DAZ')
  const field = document.querySelector<HTMLInputElement>('input[aria-label="Tags 2"]')!
  await act(async () => field.focus())
  await input(field, 'General')
  expect(checks().map(check=>check.checked)).toEqual([true])
  await input(field, 'Renamed')
  await click(button('Save'))
  expect(onSave).toHaveBeenLastCalledWith({...catalogSettings,tags:['General','Renamed','Blender'],defaultTags:['Renamed','Blender']})
  await input(document.querySelector('input[type=search]')!, '')
  await click(document.querySelector('button[aria-label="Remove tag General"]')!)
  expect(checks().map(check=>check.checked)).toEqual([true,true])
  await click(document.querySelector('button[aria-label="Remove tag Renamed"]')!)
  expect(checks().map(check=>check.checked)).toEqual([true])
  await click(checks()[0])
  await click(button('Save'))
  expect(onSave).toHaveBeenLastCalledWith({...catalogSettings,tags:['Blender'],defaultTags:[]})
})

it('keeps default edits local on Cancel and retains them after a failed save', async () => {
  const onSave = vi.fn(async () => {throw appError('workspaceUnavailable')}), onClose=vi.fn()
  dispose = (await mount(<SettingsDialog settings={catalogSettings} initialTab="stores" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={onClose} onSave={onSave} />)).unmount
  await click(document.querySelector('input[type=radio]')!)
  await click(button('Save'))
  expect(onSave).toHaveBeenCalledWith({...catalogSettings,defaultStore:'First'})
  expect(document.querySelector<HTMLInputElement>('input[type=radio]')!.checked).toBe(true)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('The workspace could not be loaded.')
  expect(onClose).not.toHaveBeenCalled()
  await click(button('Cancel'))
  expect(onClose).toHaveBeenCalledOnce()
  expect(catalogSettings.defaultStore).toBe('Second')
})

it('adds unselected defaults and reveals the default field identified by Rust', async () => {
  const onSave = vi.fn()
  dispose = (await mount(<SettingsDialog settings={catalogSettings} initialTab="stores" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={()=>{}} onSave={onSave} />)).unmount
  await click(button('Add store'))
  expect(document.activeElement?.getAttribute('aria-label')).toBe('Store 4')
  expect([...document.querySelectorAll<HTMLInputElement>('input[type=radio]')].map(r=>r.checked)).toEqual([false,true,false,false])
  await input(document.querySelector('input[type=search]')!, 'First')
  vi.mocked(api.validateSettings).mockResolvedValue([{...appError('defaultStoreInvalid'),tab:'stores',index:1,field:'default',severity:'error'}])
  await click(button('Save'))
  expect(document.activeElement?.getAttribute('type')).toBe('radio')
  expect(document.activeElement?.getAttribute('aria-invalid')).toBe('true')
  expect(onSave).not.toHaveBeenCalled()
})

it('restores selection focus after an asynchronous save without stealing an outside focus', async () => {
  let saving = deferred<void>()
  function Probe() {
    const [busy, setBusy] = useState(false)
    const [value, setValue] = useState('flat')
    return <><SingleSelect id="organization" label="Output organization" value={value} disabled={busy}
      options={outputOrganizationOptions(translator('en'))} onChange={next => {
        setBusy(true)
        void saving.promise.then(() => setValue(next)).finally(() => setBusy(false))
      }} /><button id="outside">Outside</button></>
  }
  dispose = (await mount(<Probe />)).unmount
  await click(document.getElementById('organization')!)
  await click(document.querySelector('[role="option"][aria-selected="false"]')!)
  const trigger = document.getElementById('organization') as HTMLButtonElement
  expect(trigger.disabled).toBe(true)
  // Browsers blur disabled controls; jsdom needs that native behavior simulated.
  trigger.blur()
  await act(async () => saving.resolve())
  expect(document.activeElement).toBe(trigger)
  saving = deferred<void>()
  await click(trigger)
  await click(document.querySelector('[role="option"][aria-selected="false"]')!)
  const outside = document.getElementById('outside')!
  outside.focus()
  await act(async () => saving.resolve())
  expect(document.activeElement).toBe(outside)
})

it('reveals a filtered invalid store and focuses the cell identified by Rust', async () => {
  const onSave = vi.fn()
  const view = await mount(<SettingsDialog settings={{...testSettings,stores:[{name:'Visible',prefix:'RO'},{name:'Hidden',prefix:'X'}],defaultStore:'Visible'}} initialTab="stores" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={()=>{}} onSave={onSave} />)
  dispose=view.unmount
  await input(document.querySelector<HTMLInputElement>('input[type=search]')!, 'Visible')
  expect(document.querySelector('input[aria-label="Store 2"]')).toBeNull()
  vi.mocked(api.validateSettings).mockResolvedValue([{...appError('invalidPrefix'),tab:'stores',index:1,field:'prefix',severity:'error'}])
  await click(button('Save'))
  expect(onSave).not.toHaveBeenCalled()
  expect(document.activeElement?.getAttribute('aria-label')).toBe('Prefix 2')
  expect(document.querySelector('input[aria-label="Prefix 2"]')?.getAttribute('aria-invalid')).toBe('true')
  expect(document.body.textContent).toContain('1–7 characters')
})

it('focuses an already visible invalid field after delayed validation reenables it', async () => {
  const view = await mount(<SettingsDialog settings={testSettings} initialTab="stores" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={()=>{}} onSave={async()=>{}} />)
  dispose=view.unmount
  const validation = deferred<Awaited<ReturnType<typeof api.validateSettings>>>()
  vi.mocked(api.validateSettings).mockReturnValue(validation.promise)
  await click(button('Save'))
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Prefix 1"]')?.matches(':disabled')).toBe(true)
  await act(async () => validation.resolve([{...appError('invalidPrefix'),tab:'stores',index:0,field:'prefix',severity:'error'}]))
  expect(document.activeElement?.getAttribute('aria-label')).toBe('Prefix 1')
})

it('freezes a folder-name submission until validation finishes and retains the invalid draft', async () => {
  const validation = deferred<Awaited<ReturnType<typeof api.validateEntryName>>>()
  vi.spyOn(api,'validateEntryName').mockReturnValue(validation.promise)
  const confirm = vi.fn(async () => {})
  const view = await mount(<NameDialog type="folder" t={translator('en')} onClose={()=>{}} onConfirm={confirm} />)
  dispose=view.unmount
  const field = document.querySelector<HTMLInputElement>('input')!
  await input(field,'CON .txt')
  await click(button('Create'))
  expect(field.readOnly).toBe(true)
  await act(async () => validation.resolve(appError('reservedNativeName',{name:'CON .txt'})))
  expect(field.readOnly).toBe(false)
  expect(document.activeElement).toBe(field)
  expect(field.value).toBe('CON .txt')
  expect(confirm).not.toHaveBeenCalled()
  expect(document.body.textContent).toContain('Reserved Windows filename: CON .txt')
})

it('keeps language edits in the draft, with Escape confined to the language popup', async () => {
  const onSave = vi.fn(async () => {}), onClose = vi.fn()
  const view = await mount(<SettingsDialog settings={testSettings} initialTab="general" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={onClose} onSave={onSave} />)
  dispose=view.unmount
  const language = document.querySelector<HTMLButtonElement>('[aria-label="Language"]')!
  await click(language)
  await click(document.querySelector('[role=option][data-value="en"]') ?? [...document.querySelectorAll('[role=option]')].find(option => option.textContent?.includes('English'))!)
  expect(language.textContent).toContain('English')
  await click(language)
  await act(async () => document.activeElement!.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})))
  expect(document.querySelector('[role=listbox]')).toBeNull()
  expect(document.activeElement).toBe(language)
  expect(onClose).not.toHaveBeenCalled()
  await click(button('Save'))
  expect(onSave).toHaveBeenCalledWith({...testSettings,language:'en'})
  expect(testSettings.language).toBe('de')
})

it('keeps IPC failures separate from field validation and preserves an unsaved draft on Cancel', async () => {
  const onSave = vi.fn(), onClose = vi.fn()
  const view = await mount(<SettingsDialog settings={testSettings} initialTab="stores" t={translator('en')} update={null} updateStatus="" onCheck={()=>{}} onClose={onClose} onSave={onSave} />)
  dispose=view.unmount
  await input(document.querySelector<HTMLInputElement>('input[aria-label="Store 1"]')!, 'Changed')
  vi.mocked(api.validateSettings).mockRejectedValue(appError('workspaceUnavailable'))
  await click(button('Save'))
  expect(document.body.textContent).toContain('The workspace could not be loaded.')
  expect(document.querySelector('input[aria-invalid=true]')).toBeNull()
  expect(onSave).not.toHaveBeenCalled()
  await click(button('Cancel'))
  expect(onClose).toHaveBeenCalledOnce()
  expect(testSettings.stores[0].name).toBe('LOCAL USER')
})
