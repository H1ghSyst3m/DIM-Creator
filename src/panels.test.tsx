import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { FileEntry, Package } from './api'
import { translator } from './messages'
import { StatusButton } from './StatusButton'
import { DirectoryTree, readDirectoryTree } from './FilePanel'
import { ValidationDialog } from './dialogs'

const item: Package = {
  id: 'test', name: 'Garden', store: 'LOCAL USER', prefix: 'LOCAL', autoPrefix: true,
  sku: '1', part: '01', guid: '16b2ad76-fd22-4cab-a43a-3014671584fd', tags: [],
  cover: null, cleanSupport: true, excluded: [],
}

describe('package views', () => {
  it('forgets removed branches on refresh and does not read hidden expanded descendants', async () => {
    const folder = (path: string): FileEntry => ({path,name:path.split('/').pop()!,directory:true,kind:'folder',size:0})
    const paths: string[] = []
    const data = new Map([['',[folder('Runtime')]],['Runtime',[folder('Runtime/Textures')]],['Runtime/Textures',[]]])
    const read = async (path: string) => { paths.push(path); if (!data.has(path)) throw new Error('Missing directory'); return data.get(path)! }
    const removed = await readDirectoryTree(read,'Runtime/Deleted',new Set(['','Runtime','Runtime/Deleted']))
    expect(removed.errors.size).toBe(0)
    expect(removed.expanded.has('Runtime/Deleted')).toBe(false)
    expect(paths).not.toContain('Runtime/Deleted')
    paths.length = 0
    await readDirectoryTree(read,'',new Set(['Runtime','Runtime/Textures']))
    expect(paths).toEqual([''])
  })
  it('renders ordinary Windows folders whose names match object properties', () => {
    const entries: FileEntry[] = [
      { name: 'constructor', path: 'constructor', kind: 'folder', directory: true, size: 0 },
      { name: 'toString', path: 'toString', kind: 'folder', directory: true, size: 0 },
    ]
    const unused = () => undefined
    const markup = renderToStaticMarkup(<DirectoryTree
      t={translator('en')} folder="" expanded={new Set([''])} folders={new Map([['', entries]])}
      onFolder={unused} onToggle={unused} onContext={unused}
    />)
    expect(markup).toContain('title="Content/constructor"')
    expect(markup).toContain('title="Content/toString"')
    expect(markup).toContain('role="tree"')
    expect(markup).toContain('aria-label="Expand constructor"')
  })

  it('keeps loaded children hidden when their parent is collapsed, independently of navigation', () => {
    const runtime: FileEntry = {name:'Runtime',path:'Runtime',kind:'folder',directory:true,size:0}
    const textures: FileEntry = {...runtime,name:'Textures',path:'Runtime/Textures'}
    const folders = new Map([['',[runtime]],['Runtime',[textures]],['Runtime/Textures',[]]])
    const render = (expanded: string[]) => renderToStaticMarkup(<DirectoryTree folders={folders} folder="Runtime" expanded={new Set(expanded)} t={translator('en')}
      onToggle={() => undefined} onFolder={() => undefined} onContext={() => undefined} />)
    expect(render(['','Runtime'])).toContain('aria-label="Textures"')
    const collapsed = render([''])
    expect(collapsed).not.toContain('aria-label="Textures"')
    expect(collapsed).toMatch(/aria-label="Runtime"[^>]*aria-expanded="false" aria-selected="true"/)
    expect(render(['','Runtime'])).toContain('aria-label="Textures"')
    expect(render([])).not.toContain('aria-label="Runtime"')
  })

  it('shows the complete validation checklist, including valid and optional fields', () => {
    const markup = renderToStaticMarkup(<ValidationDialog item={item} preview={{filename:'LOCAL00000001-01_Garden.zip',entries:['Manifest.dsx','Supplement.dsx','Content/People/Test.duf'],size:1,
      issues:[{field:'sku',code:'invalidSku',severity:'error',params:{}, details:null, causes:[]}]}} t={translator('en')} onClose={() => undefined} onJump={() => undefined} />)
    expect(markup).toContain('Validation results')
    expect(markup).toContain('Go to GUID')
    expect(markup).toContain('Go to SKU')
    expect(markup).toContain('No cover')
    expect(markup).toContain('1 file in package')
    expect(markup).toContain('1–8 digits, greater than 0.')
    expect(markup.match(/class="validation-row /g)).toHaveLength(11)
  })

  it('shows localized singular error and plural warning text', () => {
    const preview = {
      filename: null, entries: [], size: 0,
      issues: [
        { code: 'invalidSku', field: 'sku', params: {}, details: null, causes: [], severity: 'error' as const },
        { code: 'supportExcluded', field: 'support', params: {}, details: null, causes: [], severity: 'warning' as const },
        { code: 'warning', field: 'tags', params: {}, details: null, causes: [], severity: 'warning' as const },
      ],
    }
    expect(renderToStaticMarkup(<StatusButton preview={preview} t={translator('de')} onClick={() => undefined} />))
      .toContain('1 Fehler · 2 Warnungen')
    expect(renderToStaticMarkup(<StatusButton preview={preview} t={translator('en')} onClick={() => undefined} />))
      .toContain('1 error · 2 warnings')
  })

  it('inflects worklist totals in German and English', () => {
    expect(translator('de')('allPackages', { count: 1 })).toBe('1 Paket')
    expect(translator('de')('allPackages', { count: 2 })).toBe('2 Pakete')
    expect(translator('en')('allPackages', { count: 1 })).toBe('1 package')
    expect(translator('en')('allPackages', { count: 0 })).toBe('0 packages')
  })
})
