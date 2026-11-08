// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { App } from './App'
import { api } from './api'
import { appError } from './messages'
import { click, deferred, mount, testSettings } from './testSupport'

let dispose: (() => void) | undefined
const settings = {...testSettings, language:'en'}
const button = (label: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(el=>el.textContent===label)!
const trigger = () => document.querySelector<HTMLButtonElement>('.header-actions [aria-haspopup="menu"]')!
const choice = (id: string) => document.querySelector(`[data-theme-id="${id}"] input`)!
beforeEach(()=>{
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
  vi.stubGlobal('ResizeObserver',class {observe(){} disconnect(){}})
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')}
  HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')}
  HTMLElement.prototype.scrollIntoView=vi.fn()
  vi.spyOn(api,'loadWorkspace').mockResolvedValue({packages:[],settings,warnings:[],worklist:{revision:0,entries:[],canUndo:false,saveError:null,warnings:[]}})
  vi.spyOn(api,'previewOutputs').mockResolvedValue([])
  vi.spyOn(api,'validateSettings').mockResolvedValue([])
  vi.spyOn(api,'saveSettings').mockImplementation(async next=>next)
})
afterEach(()=>{dispose?.();dispose=undefined;vi.restoreAllMocks();vi.unstubAllGlobals()})

it('selects themes directly from grouped header entries and skips the current theme',async()=>{
  dispose=(await mount(<App/>)).unmount
  for(const id of ['dark','sand','graphite','polar','evergreen','light','frost','nocturne','nocturne','dark']) {
    await click(trigger())
    const menu=document.querySelector('[role=menu]')!
    expect([...menu.querySelectorAll('[role=group]')].map(el=>el.getAttribute('aria-label'))).toEqual(['Classic · Light','Classic · Dark','Glass · Light','Glass · Dark'])
    expect(menu.querySelectorAll('[role=menuitemradio]')).toHaveLength(8)
    expect(menu.querySelector(`[data-theme-id="${id}"] .theme-swatch`)?.getAttribute('data-theme-category')).toBe(['frost','nocturne'].includes(id)?'glass':'classic')
    await click(menu.querySelector(`[data-theme-id="${id}"]`)!)
    expect(document.documentElement.dataset.theme).toBe(id)
    expect(document.documentElement.dataset.themeCategory).toBe(['frost','nocturne'].includes(id)?'glass':'classic')
    expect(document.querySelector('[role=menu]')).toBeNull()
  }
  expect(vi.mocked(api.saveSettings).mock.calls.map(([value])=>value.theme)).toEqual(['sand','graphite','polar','evergreen','light','frost','nocturne','dark'])
})

it('retains the saved header theme on failure and respects keyboard and outside dismissal',async()=>{
  dispose=(await mount(<App/>)).unmount
  await click(trigger())
  await act(async()=>document.activeElement!.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true})))
  expect(document.activeElement?.getAttribute('data-theme-id')).toBe('nocturne')
  await act(async()=>document.activeElement!.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})))
  expect(document.activeElement).toBe(trigger())
  await click(trigger())
  await act(async()=>document.activeElement!.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true})))
  expect(document.querySelector('[role=menu]')).toBeNull()
  expect(document.activeElement).toBe(trigger())
  await click(trigger())
  await act(async()=>document.body.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true})))
  expect(document.querySelector('[role=menu]')).toBeNull()
  const saving=deferred<typeof settings>()
  vi.mocked(api.saveSettings).mockReturnValueOnce(saving.promise)
  await click(trigger())
  await click(document.querySelector('[data-theme-id="frost"]')!)
  expect(trigger().disabled).toBe(true)
  expect(document.documentElement.dataset.theme).toBe('dark')
  await act(async()=>saving.reject(appError('ioError')))
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(document.documentElement.dataset.themeCategory).toBe('classic')
  expect(document.querySelector('[role=alert]')).not.toBeNull()
})

it.each([
  ['polar', 'Cancel'],
  ['frost', 'Close'],
  ['nocturne', 'Escape'],
])('discards the %s settings preview through %s', async (preview, dismissal) => {
  dispose=(await mount(<App/>)).unmount
  const opener=document.querySelector<HTMLButtonElement>('[aria-label="Settings"]')!
  opener.focus();await click(opener)
  await click(choice(preview))
  expect(document.documentElement.dataset.theme).toBe(preview)
  expect(document.documentElement.dataset.themeCategory).toBe(preview==='polar'?'classic':'glass')
  await click(button('Stores & prefixes'));await click(button('General'))
  expect((choice(preview) as HTMLInputElement).checked).toBe(true)
  expect(api.saveSettings).not.toHaveBeenCalled()
  if(dismissal==='Escape')await act(async()=>document.querySelector('dialog')!.dispatchEvent(new Event('cancel',{cancelable:true})))
  else await click(dismissal==='Close' ? document.querySelector('dialog [aria-label="Close"]')! : button('Cancel'))
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(document.documentElement.dataset.themeCategory).toBe('classic')
  expect(document.activeElement).toBe(opener)
  await click(opener)
  expect((choice('dark') as HTMLInputElement).checked).toBe(true)
  expect(api.saveSettings).not.toHaveBeenCalled()
})

for(const preview of ['sand','frost','nocturne']) it(`keeps the ${preview} preview and other draft values on save failure, then commits once on retry`,async()=>{
  dispose=(await mount(<App/>)).unmount
  await click(document.querySelector('[aria-label="Settings"]')!)
  await click(choice(preview))
  await click(document.querySelector('[aria-label="Language"]')!)
  await click([...document.querySelectorAll('[role=option]')].find(el=>el.textContent==='Deutsch')!)
  vi.mocked(api.saveSettings).mockRejectedValueOnce(appError('ioError'))
  await click(button('Save'))
  expect(document.querySelector('dialog [role=alert]')).not.toBeNull()
  expect(document.documentElement.dataset.theme).toBe(preview)
  expect(document.querySelector('[aria-label="Language"]')?.textContent).toBe('Deutsch')
  await click(button('Save'))
  expect(document.querySelector('dialog')).toBeNull()
  expect(document.documentElement.dataset.theme).toBe(preview)
  expect(api.saveSettings).toHaveBeenLastCalledWith({...settings,theme:preview,language:'de'})
  await click(document.querySelector('[aria-label="Einstellungen"]')!)
  expect((choice(preview) as HTMLInputElement).checked).toBe(true)
})

for(const saved of ['frost','nocturne']) it(`restores the saved ${saved} category after cancelling a Classic preview`,async()=>{
  vi.mocked(api.loadWorkspace).mockResolvedValue({packages:[],settings:{...settings,theme:saved},warnings:[],worklist:{revision:0,entries:[],canUndo:false,saveError:null,warnings:[]}})
  dispose=(await mount(<App/>)).unmount
  expect(document.documentElement.dataset.theme).toBe(saved)
  expect(document.documentElement.dataset.themeCategory).toBe('glass')
  await click(document.querySelector('[aria-label="Settings"]')!)
  await click(choice('light'))
  expect(document.documentElement.dataset.themeCategory).toBe('classic')
  await click(button('Cancel'))
  expect(document.documentElement.dataset.theme).toBe(saved)
  expect(document.documentElement.dataset.themeCategory).toBe('glass')
  expect(api.saveSettings).not.toHaveBeenCalled()
})
