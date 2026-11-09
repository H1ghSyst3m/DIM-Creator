// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { revealItemInDir } from '@tauri-apps/plugin-opener'
import type { ExportResult, ExportRow } from './api'
import { ExportProgress } from './dialogs'
import { translator } from './messages'
import { click, deferred, mount } from './testSupport'

vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: vi.fn(), revealItemInDir: vi.fn() }))

const results: ExportResult[] = [
  { id: 'a', status: 'success', path: 'C:\\DIM Ausgabe\\Äpfel.zip', message: null },
  { id: 'b', status: 'success', path: 'C:\\DIM Ausgabe\\2026-11-09\\Bäume.zip', message: null },
]
const rows: ExportRow[] = [
  { id: 'a', name: 'Äpfel', filename: 'Äpfel.zip', outputDir: 'C:/Old', outputPath: 'C:/Old/Äpfel.zip', issues: [], existing: false, stamp: null },
  { id: 'b', name: 'Bäume', filename: 'Bäume.zip', outputDir: 'C:/Old', outputPath: 'C:/Old/Bäume.zip', issues: [], existing: false, stamp: null },
]
let dispose: (() => void) | undefined

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
  vi.mocked(revealItemInDir).mockResolvedValue()
})
afterEach(() => { dispose?.(); dispose = undefined; vi.resetAllMocks(); vi.unstubAllGlobals() })

async function renderResults(language = 'de', entries: ExportResult[] | null = results) {
  const view = await mount(<ExportProgress rows={rows} event={null} results={entries} cancelling={false}
    error={undefined} t={translator(language)} onClose={vi.fn()} onCancel={vi.fn()} />)
  dispose = view.unmount
  return view.host
}

it.each([
  ['de', 'Im Explorer anzeigen'],
  ['en', 'Show in Explorer'],
])('reveals the chosen result path with an accessible package label in %s', async (language, label) => {
  const host = await renderResults(language)
  const buttons = [...host.querySelectorAll<HTMLButtonElement>('li button')]
  expect(buttons).toHaveLength(2)
  expect(buttons.map(button => button.textContent)).toEqual([label, label])
  expect(buttons.map(button => button.getAttribute('aria-label'))).toEqual([`${label}: Äpfel`, `${label}: Bäume`])
  expect(revealItemInDir).not.toHaveBeenCalled()
  await click(buttons[1])
  expect(revealItemInDir).toHaveBeenCalledExactlyOnceWith('C:\\DIM Ausgabe\\2026-11-09\\Bäume.zip')
  await click(buttons[0])
  expect(revealItemInDir).toHaveBeenNthCalledWith(2, 'C:\\DIM Ausgabe\\Äpfel.zip')
  expect(host.querySelectorAll('li .success')).toHaveLength(2)
})

it('offers the action only for successful results with a path', async () => {
  const host = await renderResults('de', [
    ...results,
    { id: 'failed', status: 'failed', path: null, message: null },
    { id: 'skipped', status: 'skipped', path: null, message: null },
    { id: 'cancelled', status: 'cancelled', path: null, message: null },
    { id: 'no-path', status: 'success', path: null, message: null },
  ])
  const entries = [...host.querySelectorAll('li')]
  expect(entries.map(entry => entry.querySelectorAll('button').length)).toEqual([1, 1, 0, 0, 0, 0])
})

it('disables explorer actions until the native request finishes', async () => {
  const pending = deferred<void>()
  vi.mocked(revealItemInDir).mockReturnValueOnce(pending.promise)
  const host = await renderResults()
  const buttons = [...host.querySelectorAll<HTMLButtonElement>('li button')]
  expect(buttons).toHaveLength(2)
  await click(buttons[0])
  expect(buttons.every(button => button.disabled)).toBe(true)
  await click(buttons[1])
  expect(revealItemInDir).toHaveBeenCalledTimes(1)
  await act(async () => pending.resolve())
  expect(buttons.every(button => !button.disabled)).toBe(true)
})

it('shows a native failure beside its package without changing the export status and allows retry', async () => {
  vi.mocked(revealItemInDir).mockRejectedValueOnce(new Error('Access denied'))
  const host = await renderResults()
  const buttons = [...host.querySelectorAll<HTMLButtonElement>('li button')]
  expect(buttons).toHaveLength(2)
  await click(buttons[1])
  const entries = [...host.querySelectorAll('li')]
  expect(entries[0].querySelector('[role="alert"]')).toBeNull()
  expect(entries[1].querySelector('[role="alert"]')?.textContent).toContain('Access denied')
  expect(entries[1].querySelector('.success')?.textContent).toBe('Erstellt')
  expect(buttons[1].disabled).toBe(false)
  await click(buttons[1])
  expect(revealItemInDir).toHaveBeenCalledTimes(2)
  expect(host.querySelector('[role="alert"]')).toBeNull()
})

it('offers no explorer action while the export is still running', async () => {
  const host = await renderResults('de', null)
  expect(host.querySelectorAll('li button')).toHaveLength(0)
  expect(host.querySelector('progress')).not.toBeNull()
  expect(revealItemInDir).not.toHaveBeenCalled()
})
