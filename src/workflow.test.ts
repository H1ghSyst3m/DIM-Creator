import { describe, expect, it } from 'vitest'
import { excludedBy, filterPackages, markVisible, reviewedExport, validExportIds, workspacePreviews } from './workflow'

describe('independent package selection', () => {
  const items = [
    { id: 'a', name: 'Garden Set', sku: '251', part: '02' },
    { id: 'b', name: 'Garden House', sku: '253', part: '01' },
    { id: 'c', name: 'Night Sky', sku: '471', part: '03' },
  ]

  it('searches name, SKU, and displayed part number', () => {
    expect(filterPackages(items, ' garden ').map(item => item.id)).toEqual(['a', 'b'])
    expect(filterPackages(items, '471').map(item => item.id)).toEqual(['c'])
    expect(filterPackages([{ ...items[0], part: '2' }], '02')).toHaveLength(1)
  })

  it('keeps previously checked items when selecting and deselecting visible rows', () => {
    const selected = markVisible(new Set(['c']), items.slice(0, 2), true)
    expect([...selected]).toEqual(['c', 'a', 'b'])
    expect([...markVisible(selected, items.slice(0, 2), false)]).toEqual(['c'])
  })
})

describe('package exports', () => {
  it('warns about case-insensitive workspace name collisions without blocking a single export', () => {
    const preview = {filename:'Package.zip',entries:[],issues:[],size:0}
    const source = {a:preview,b:{...preview,filename:'package.zip'},c:{...preview,filename:'other.zip'}}
    const targets = Object.fromEntries(['a','b','c'].map(id => [id,{id,directory:'C:/Output',key:'C:/OUTPUT'}]))
    const result = workspacePreviews(source, targets)
    expect(result.a.issues[0]).toMatchObject({field:'output',severity:'warning',code:'duplicateWorkspaceOutput'})
    expect(result.b.issues).toHaveLength(1)
    expect(result.c.issues).toHaveLength(0)
    expect(source.a.issues).toHaveLength(0)
    expect(validExportIds([{id:'a',issues:result.a.issues}])).toEqual(['a'])
    expect(workspacePreviews(source, {...targets,b:{id:'b',directory:'C:/Other',key:'C:/OTHER'}}).a.issues).toEqual([])
    expect(workspacePreviews(source, {}).a.issues).toEqual([])
  })
  it('keeps warnings eligible for export and excludes errors', () => {
    expect(validExportIds([
      { id: 'a', issues: [{ severity: 'warning' }] },
      { id: 'b', issues: [{ severity: 'error' }] },
    ])).toEqual(['a'])
  })

  it('starts only confirmed rows and includes the exact observed replacement stamp', () => {
    const row = { name: 'Package', filename: 'item.zip', outputDir:'C:/Output',outputPath:'C:/Output/item.zip', existing: false, stamp: null, issues: [] }
    const rows = [
      { ...row, id: 'new' },
      { ...row, id: 'declined', existing: true, stamp: 'before' },
      { ...row, id: 'approved', existing: true, stamp: 'observed' },
      { ...row, id: 'unreadable', existing: true },
      { ...row, id: 'invalid', issues: [{ code: 'invalidSku', field: 'sku', severity: 'error' as const, params: {}, details: null, causes: [] }] },
    ]
    expect(reviewedExport(rows, new Set(['approved', 'unreadable', 'invalid']))).toEqual({
      ids: ['new', 'approved'],
      replace: [{ id: 'approved', outputPath: 'C:/Output/item.zip', stamp: 'observed' }],
    })
  })

  it('applies exclusions to descendants and keeps matching siblings', () => {
    const packageData = { cleanSupport: true, excluded: ['data/old'] }
    expect(excludedBy(packageData, 'Data/Old/content.duf')).toBe('ancestor')
    expect(excludedBy(packageData, 'data/old-test/keep.duf')).toBeNull()
    expect(excludedBy(packageData, 'runtime/Support/meta.dsx')).toBe('support')
    expect(excludedBy(packageData, 'data/old')).toBe('self')
  })
})
