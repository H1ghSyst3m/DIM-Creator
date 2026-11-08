// @vitest-environment jsdom
import { act } from 'react'
import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { clearMocks, mockIPC, mockWindows } from '@tauri-apps/api/mocks'
import { App } from './App'
import { api } from './api'
import { click, deferred, mount, testSettings } from './testSupport'
import capability from '../src-tauri/capabilities/main.json'
import config from '../src-tauri/tauri.conf.json'

let dispose: (() => void) | undefined
let nativeState = false
const read = vi.fn(async () => nativeState)
const write = vi.fn(async (value: boolean) => { nativeState = value })
const commands: string[] = []
const pin = () => document.querySelector<HTMLButtonElement>('.header-actions [aria-pressed]')!

beforeEach(() => {
  nativeState = false
  commands.length = 0
  read.mockReset().mockImplementation(async () => nativeState)
  write.mockReset().mockImplementation(async value => { nativeState = value })
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('isTauri', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  mockWindows('main')
  mockIPC((command, payload) => {
    commands.push(command)
    if (command === 'plugin:window|is_always_on_top') return read()
    if (command === 'plugin:window|set_always_on_top') {
      expect(payload).toEqual({label:'main', value:expect.any(Boolean)})
      return write((payload as {value:boolean}).value)
    }
    if (command === 'plugin:event|listen') return 1
  })
  vi.spyOn(api, 'loadWorkspace').mockResolvedValue({packages:[],settings:{...testSettings,language:'en'},warnings:[],worklist:{revision:0,entries:[],canUndo:false,saveError:null,warnings:[]}})
  vi.spyOn(api, 'previewOutputs').mockResolvedValue([])
  vi.spyOn(api, 'onDrop').mockResolvedValue(() => {})
  vi.spyOn(api, 'saveSettings').mockImplementation(async settings => settings)
  vi.spyOn(api, 'savePackage').mockResolvedValue()
})
afterEach(() => { dispose?.(); dispose=undefined; clearMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('places a session-only toggle between theme and settings and reflects successful window commands', async () => {
  dispose = (await mount(<App/>)).unmount
  expect(pin()).not.toBeNull()
  expect(pin().previousElementSibling?.getAttribute('aria-haspopup')).toBe('menu')
  expect(pin().nextElementSibling?.getAttribute('aria-label')).toBe('Settings')
  expect(pin().getAttribute('aria-pressed')).toBe('false')
  expect(pin().getAttribute('aria-label')).toBe('Enable always on top')
  const inactiveIcon = pin().querySelector('svg')!.innerHTML
  await click(pin())
  expect(nativeState).toBe(true)
  expect(pin().getAttribute('aria-pressed')).toBe('true')
  expect(pin().title).toBe('Disable always on top')
  expect(pin().querySelector('svg')!.innerHTML).not.toBe(inactiveIcon)
  await click(pin())
  expect(nativeState).toBe(false)
  expect(pin().getAttribute('aria-pressed')).toBe('false')
  expect(api.saveSettings).not.toHaveBeenCalled()
  expect(api.savePackage).not.toHaveBeenCalled()
  expect(write.mock.calls).toEqual([[true],[false]])
  expect(capability.windows).toEqual(['main'])
  expect(capability.permissions).toContain('core:default')
  for (const command of commands.filter(command => command.startsWith('plugin:window|set_'))) {
    expect(capability.permissions).toContain(`core:window:allow-${command.split('|')[1].replaceAll('_','-')}`)
  }
  expect(config.app.windows[0]).not.toHaveProperty('alwaysOnTop', true)
})

it('retains focus and the confirmed state during a pending command and ignores rapid clicks and focus refreshes', async () => {
  dispose = (await mount(<App/>)).unmount
  const pending = deferred<void>()
  write.mockImplementationOnce(async value => { await pending.promise; nativeState=value })
  pin().focus()
  await act(async () => {
    pin().click(); pin().click()
    window.dispatchEvent(new Event('focus'))
  })
  expect(write).toHaveBeenCalledTimes(1)
  expect(pin().getAttribute('aria-pressed')).toBe('false')
  expect(pin().getAttribute('aria-disabled')).toBe('true')
  expect(pin().getAttribute('aria-busy')).toBe('true')
  expect(document.activeElement).toBe(pin())
  const reads = read.mock.calls.length
  await click(pin())
  expect(read).toHaveBeenCalledTimes(reads)
  await act(async () => pending.resolve())
  expect(pin().getAttribute('aria-pressed')).toBe('true')
  expect(pin().getAttribute('aria-disabled')).toBe('false')
  expect(document.activeElement).toBe(pin())
})

it('reads actual state on mount, focus and before toggling instead of trusting a stale button', async () => {
  nativeState = true
  dispose = (await mount(<App/>)).unmount
  expect(pin().getAttribute('aria-pressed')).toBe('true')
  nativeState = false
  await act(async () => window.dispatchEvent(new Event('focus')))
  expect(pin().getAttribute('aria-pressed')).toBe('false')
  nativeState = true
  await click(pin())
  expect(write).toHaveBeenCalledExactlyOnceWith(false)
  expect(pin().getAttribute('aria-pressed')).toBe('false')
})

it('reports rejected commands, rechecks actual state and permits a retry', async () => {
  dispose = (await mount(<App/>)).unmount
  write.mockRejectedValueOnce('permission denied')
  await click(pin())
  expect(pin().getAttribute('aria-pressed')).toBe('false')
  expect(read).toHaveBeenCalledTimes(3)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('permission denied')
  await click(pin())
  expect(pin().getAttribute('aria-pressed')).toBe('true')
  expect(api.saveSettings).not.toHaveBeenCalled()
})

it('retains the last confirmed state when both the command and recovery query fail', async () => {
  nativeState = true
  dispose = (await mount(<App/>)).unmount
  read.mockResolvedValueOnce(true).mockRejectedValueOnce('query unavailable')
  write.mockRejectedValueOnce('command unavailable')
  await click(pin())
  expect(pin().getAttribute('aria-pressed')).toBe('true')
  expect(document.querySelector('[role=alert]')?.textContent).toContain('command unavailable')
  await click(pin())
  expect(pin().getAttribute('aria-pressed')).toBe('false')
})

it('does not issue a write if the initial or pre-toggle query fails', async () => {
  read.mockRejectedValueOnce('initial read failed')
  dispose = (await mount(<App/>)).unmount
  expect(document.querySelector('[role=alert]')).not.toBeNull()
  read.mockRejectedValueOnce('read failed')
  await click(pin())
  expect(write).not.toHaveBeenCalled()
  await click(pin())
  expect(pin().getAttribute('aria-pressed')).toBe('true')
})

it('continues to toggle during an unrelated settings save without adding settings writes', async () => {
  dispose = (await mount(<App/>)).unmount
  const saving = deferred<typeof testSettings>()
  vi.mocked(api.saveSettings).mockReturnValueOnce(saving.promise)
  const theme = document.querySelector('.header-actions [aria-haspopup=menu]')!
  await click(theme)
  await click(document.querySelector('[data-theme-id=frost]')!)
  expect((theme as HTMLButtonElement).disabled).toBe(true)
  await click(pin())
  expect(pin().getAttribute('aria-pressed')).toBe('true')
  expect(api.saveSettings).toHaveBeenCalledTimes(1)
  expect(api.savePackage).not.toHaveBeenCalled()
  await act(async () => saving.resolve({...testSettings,language:'en',theme:'frost'}))
})

it('disposes focus listeners and does not continue a pending toggle after unmount', async () => {
  dispose = (await mount(<App/>)).unmount
  const pending = deferred<boolean>()
  read.mockReturnValueOnce(pending.promise)
  await click(pin())
  dispose(); dispose=undefined
  await act(async () => pending.resolve(false))
  expect(write).not.toHaveBeenCalled()
  const reads = read.mock.calls.length
  window.dispatchEvent(new Event('focus'))
  expect(read).toHaveBeenCalledTimes(reads)
})

it('uses German action labels and starts from the new window state after remount', async () => {
  vi.mocked(api.loadWorkspace).mockResolvedValue({packages:[],settings:testSettings,warnings:[],worklist:{revision:0,entries:[],canUndo:false,saveError:null,warnings:[]}})
  dispose = (await mount(<App/>)).unmount
  expect(pin().title).toBe('Immer im Vordergrund aktivieren')
  await click(pin())
  expect(pin().getAttribute('aria-label')).toBe('Immer im Vordergrund deaktivieren')
  dispose(); nativeState=false
  dispose = (await mount(<App/>)).unmount
  expect(pin().getAttribute('aria-pressed')).toBe('false')
  expect(api.saveSettings).not.toHaveBeenCalled()
})

it('keeps the contrast-mode active outline separate from the keyboard focus outline', async () => {
  nativeState = true
  dispose = (await mount(<App/>)).unmount
  const css = readFileSync('src/styles.css','utf8')
  const forced = css.split('@media (forced-colors: active)')[1].split('\n}')[0]
  // jsdom has no keyboard-modality tracking; model keyboard focus with :focus.
  const outlineSelector = forced.match(/([^{}]+)\{\s*outline:/)![1].trim().replaceAll(':focus-visible', ':focus')
  expect(pin().matches(outlineSelector)).toBe(true)
  pin().focus()
  expect(pin().matches(':focus')).toBe(true)
  expect(pin().matches(outlineSelector)).toBe(false)
})
