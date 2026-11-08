// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { useValidation } from './useValidation'

const mounted: ReturnType<typeof createRoot>[] = []
afterEach(() => { act(() => mounted.splice(0).forEach(root => root.unmount())); vi.useRealTimers(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

it('debounces Rust validation and ignores a late response for an older draft', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const pending = new Map<string, (value: string[]) => void>()
  const validate = (name: string) => new Promise<string[]>(resolve => pending.set(name, resolve))
  let state!: ReturnType<typeof useValidation<string, string[]>>
  function Probe({ name }: { name: string }) { state = useValidation(name, validate); return <span>{state.result?.join(',')}</span> }
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host); mounted.push(root)
  await act(async () => root.render(<Probe name="old" />))
  await act(async () => { await vi.advanceTimersByTimeAsync(150) })
  await act(async () => root.render(<Probe name="new" />))
  await act(async () => { await vi.advanceTimersByTimeAsync(150); pending.get('new')!(['current']) })
  expect(host.textContent).toBe('current')
  await act(async () => pending.get('old')!(['obsolete']))
  expect(host.textContent).toBe('current')
  let result!: string[]
  await act(async () => { const check = state.check(); pending.get('new')!(['fresh']); result = await check })
  expect(result).toEqual(['fresh'])
  expect(host.textContent).toBe('fresh')
})
