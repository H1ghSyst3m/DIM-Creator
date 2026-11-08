import { afterEach, expect, it, vi } from 'vitest'
import { getCurrentWindow, type CloseRequestedEvent } from '@tauri-apps/api/window'
import { mockIPC, mockWindows, clearMocks } from '@tauri-apps/api/mocks'
import capability from '../src-tauri/capabilities/main.json'
import { registerCloseHandler } from './closeRequest'

const deferred = () => {
  let resolve!: () => void
  const promise = new Promise<void>(done => { resolve = done })
  return {promise,resolve}
}

function setup() {
  let listener!: (event: CloseRequestedEvent) => Promise<void>
  const unlisten = vi.fn()
  const options = { busy: vi.fn(() => false), flush: vi.fn(async () => {}), canClose: vi.fn(async () => true),
    onClosing: vi.fn(), onError: vi.fn() }
  const subscribe = vi.fn(async (handler: typeof listener) => { listener = handler; return unlisten })
  const dispose = registerCloseHandler(subscribe, options)
  const event = () => ({ preventDefault: vi.fn() }) as unknown as CloseRequestedEvent
  return { options, subscribe, dispose, unlisten, event, request: (event: CloseRequestedEvent) => listener(event) }
}

afterEach(() => { vi.unstubAllGlobals() })

it('authorizes the command actually used by the installed Tauri close wrapper', async () => {
  vi.stubGlobal('window', {crypto:globalThis.crypto})
  let handlerId = 0
  const commands: string[] = []
  mockIPC((command, args) => {
    commands.push(command)
    if (command === 'plugin:event|listen') { handlerId = (args as {handler:number}).handler; return 1 }
    if (command.startsWith('plugin:window|')) {
      expect(capability.permissions).toContain(`core:window:allow-${command.split('|')[1]}`)
    }
  })
  mockWindows('main')
  await getCurrentWindow().onCloseRequested(async () => {})
  // Await the real wrapper callback, including its final IPC request.
  const callbacks = (window as unknown as {__TAURI_INTERNALS__: {callbacks: Map<number, (event: unknown) => Promise<void>>}}).__TAURI_INTERNALS__.callbacks
  await callbacks.get(handlerId)!({event:'tauri://close-requested',id:1,payload:null})
  expect(commands).toContain('plugin:window|destroy')
  clearMocks()
})

it('allows an idle close and retains the interaction lock until the window closes', async () => {
  const s = setup(), event = s.event()
  await s.request(event)
  expect(event.preventDefault).not.toHaveBeenCalled()
  expect(s.options.flush).toHaveBeenCalledOnce()
  expect(s.options.canClose).toHaveBeenCalledOnce()
  expect(s.options.onClosing.mock.calls).toEqual([[true]])
})

it('waits for pending saves and suppresses repeated requests', async () => {
  const s = setup(), save = deferred(), first = s.event(), second = s.event()
  s.options.flush.mockReturnValue(save.promise)
  const closing = s.request(first)
  await s.request(second)
  expect(second.preventDefault).toHaveBeenCalledOnce()
  expect(s.options.canClose).not.toHaveBeenCalled()
  save.resolve()
  await closing
  expect(first.preventDefault).not.toHaveBeenCalled()
  expect(s.options.flush).toHaveBeenCalledOnce()
})

it('keeps the window open on save failure and permits a successful retry', async () => {
  const s = setup(), first = s.event()
  s.options.flush.mockRejectedValueOnce('disk failure')
  await s.request(first)
  expect(first.preventDefault).toHaveBeenCalledOnce()
  expect(s.options.onError).toHaveBeenCalledWith('disk failure')
  expect(s.options.onClosing).toHaveBeenLastCalledWith(false)
  const retry = s.event()
  await s.request(retry)
  expect(retry.preventDefault).not.toHaveBeenCalled()
})

it('blocks active frontend operations and checks the backend again after saving', async () => {
  const s = setup(), busy = s.event(), backendBusy = s.event()
  s.options.busy.mockReturnValueOnce(true)
  await s.request(busy)
  expect(s.options.flush).not.toHaveBeenCalled()
  s.options.canClose.mockResolvedValueOnce(false)
  await s.request(backendBusy)
  expect(busy.preventDefault).toHaveBeenCalledOnce()
  expect(backendBusy.preventDefault).toHaveBeenCalledOnce()
  expect(s.options.onError).toHaveBeenLastCalledWith(new Error('closeBusy'))
})

it('unsubscribes when disposed during registration and prevents a pending close', async () => {
  const s = setup(), save = deferred(), event = s.event()
  s.options.flush.mockReturnValue(save.promise)
  const closing = s.request(event)
  s.dispose()
  save.resolve()
  await closing
  expect(s.unlisten).toHaveBeenCalledOnce()
  expect(event.preventDefault).toHaveBeenCalledOnce()
  expect(s.options.canClose).not.toHaveBeenCalled()
})

it('reports listener registration failures', async () => {
  const onError = vi.fn()
  registerCloseHandler(async () => { throw new Error('IPC denied') }, {
    busy: () => false, flush: async () => {}, canClose: async () => true, onClosing: () => {}, onError,
  })
  await Promise.resolve()
  await Promise.resolve()
  expect(onError).toHaveBeenCalledWith('closeHandlerFailed')
})
