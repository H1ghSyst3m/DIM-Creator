import { describe, expect, it } from 'vitest'
import { selectFiles, remapFilePath } from './fileSelection'

describe('Explorer selection and paths', () => {
  it('toggles with Ctrl and extends from the last plain selection with Shift', () => {
    const order = ['a','b','c','d']
    const first = selectFiles(new Set(), null, 'b', order, false, false)
    const range = selectFiles(first.paths, first.anchor, 'd', order, false, true)
    expect([...range.paths]).toEqual(['b','c','d'])
    expect(range.anchor).toBe('b')
    expect([...selectFiles(range.paths, range.anchor, 'c', order, true, false).paths]).toEqual(['b','d'])
  })
  it('keeps case-insensitive path segments distinct from similarly named siblings', () => {
    const changes = [{from:'People/Old',to:'Props/New',kind:'move' as const}]
    expect(remapFilePath('People/Old/Textures', changes)).toBe('Props/New/Textures')
    expect(remapFilePath('People/Older', changes)).toBe('People/Older')
    expect(remapFilePath('People/Old', [{...changes[0],kind:'copy'}])).toBe('People/Old')
    expect(remapFilePath('Props/New/Child', [{from:'Props/New',to:null,kind:'delete'}])).toBe('Props')
  })
})
