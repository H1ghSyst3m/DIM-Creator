import { describe, expect, it } from 'vitest'
import { selectPackages, packageTargets, nextPackageAfterRemoval, removePackages } from './workflow'

describe('worklist selection and action targets', () => {
  const visible = ['a', 'b', 'c', 'd']
  it('toggles one mark and replaces only visible marks for a Shift range', () => {
    expect([...selectPackages(new Set(['hidden', 'a']), visible, 'a', 'c', 'range')]).toEqual(['hidden', 'a', 'b', 'c'])
    expect([...selectPackages(new Set(['hidden', 'd']), visible, 'b', 'c', 'range')]).toEqual(['hidden', 'b', 'c'])
    expect([...selectPackages(new Set(['hidden', 'a']), visible, 'a', 'a', 'toggle')]).toEqual(['hidden'])
  })
  it('adds a Ctrl+Shift range and uses only the target when the anchor is no longer visible', () => {
    expect([...selectPackages(new Set(['hidden', 'a']), visible, 'b', 'c', 'addRange')]).toEqual(['hidden', 'a', 'b', 'c'])
    expect([...selectPackages(new Set(['hidden', 'a']), visible, 'missing', 'c', 'range')]).toEqual(['hidden', 'c'])
  })
  it('captures context targets without changing unrelated batch marks', () => {
    const selected = new Set(['hidden', 'a'])
    expect(packageTargets(selected, ['a', 'b', 'hidden'], 'a')).toEqual(['a', 'hidden'])
    expect(packageTargets(selected, ['a', 'b', 'hidden'], 'b')).toEqual(['b'])
    expect([...selected]).toEqual(['hidden', 'a'])
  })
  it('preserves the active package or chooses the next remaining neighbour', () => {
    expect(nextPackageAfterRemoval(visible, 'b', new Set(['a']))).toBe('b')
    expect(nextPackageAfterRemoval(visible, 'b', new Set(['b', 'c']))).toBe('d')
    expect(nextPackageAfterRemoval(visible, 'd', new Set(['c', 'd']))).toBe('b')
    expect(nextPackageAfterRemoval(visible, 'b', new Set(visible))).toBeNull()
  })
  it('continues after a failed removal and retries only failures', async () => {
    const calls: string[] = []
    const failures = await removePackages(['a', 'b', 'c'], async id => {
      calls.push(id)
      if (id === 'b') throw new Error('locked')
    })
    expect(calls).toEqual(['a', 'b', 'c'])
    expect(failures.map(row => row.id)).toEqual(['b'])
    expect(String(failures[0].error)).toContain('locked')
    await removePackages(failures.map(row => row.id), async id => { calls.push(id) })
    expect(calls).toEqual(['a', 'b', 'c', 'b'])
  })
})
