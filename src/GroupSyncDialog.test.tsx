// @vitest-environment jsdom
import { act } from 'react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { GroupSyncDialog } from './GroupSyncDialog'
import { api, type SyncSession, type SyncPreview } from './api'
import { translator } from './messages'
import { click, deferred, mount, testPackage } from './testSupport'

const session: SyncSession = {metadata:[],outputs:[],worklist:{revision:4,entries:[],canUndo:true,saveError:null,warnings:[]},data:{group:'group',packages:[{...testPackage,id:'a',name:'Source'},{...testPackage,id:'b',name:'Target',part:'04'}],expected:{revision:4,worklistStamp:'worklist',packages:{a:'a',b:'b'}}},context:{baseDir:'C:/Output',organization:'flat',localDate:'2026-11-05'}}
const preview: SyncPreview = {changes:[],order:['b','a'],stamp:'checked',metadata:[],outputs:[],notices:[]}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
  vi.stubGlobal('ResizeObserver',class { observe() {} disconnect() {} })
  HTMLElement.prototype.scrollIntoView = vi.fn()
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
  vi.spyOn(api,'previewGroupSync').mockResolvedValue(preview)
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('shows current source values, empty-value effects and the source in the whole-group part list', async () => {
  const source = {...testPackage,id:'a',name:'A very long source name '.repeat(12),sku:'123',store:'DAZ 3D',prefix:'IM',tags:['DAZStudio4_5','Materials']}
  const target = {...testPackage,id:'b',name:'',sku:'',cleanSupport:false}
  const view = await mount(<GroupSyncDialog session={{...session,data:{...session.data,packages:[source,target]}}} source="a" t={translator('de')} onClose={vi.fn()} onReload={vi.fn()} onApply={vi.fn()} />)
  const values = () => [...view.host.querySelectorAll('.sync-source-value')].map(value=>value.textContent)
  expect(values()).toEqual(['DAZ 3D · IM · Automatisch','123','DAZStudio4_5, Materials',source.name,'Kein Cover','Aktiviert'])
  expect(view.host.querySelector('.sync-source-value[title]')?.getAttribute('title')).toBe('DAZ 3D · IM · Automatisch')
  expect(view.host.querySelector('.sync-part-head')?.textContent).toContain('Gesamte Gruppe')
  expect(view.host.querySelector('[data-sync-part="a"] .sync-source-badge')?.textContent).toBe('Quelle')
  expect(view.host.querySelector('.sync-field-grid')?.textContent).not.toContain('Entfernt Cover der Ziele.')
  await click(view.host.querySelector('#sync-source')!)
  await click(view.host.querySelector('[role="option"]:last-child')!)
  expect(values()).toEqual(['LOCAL USER · LOCAL · Automatisch','Leer','Keine Tags','Leer','Kein Cover','Deaktiviert'])
  expect(view.host.querySelector('[data-sync-part="a"] .sync-source-badge')).toBeNull()
  expect(view.host.querySelector('[data-sync-part="b"] .sync-source-badge')?.textContent).toBe('Quelle')
  expect(view.host.querySelector('.sync-field-grid')?.textContent).toContain('Entfernt Tags der Ziele.')
  await click([...view.host.querySelectorAll('button')].filter(b=>b.textContent==='Keine auswählen')[1])
  expect(view.host.querySelector('.sync-field-grid')?.textContent).not.toContain('Entfernt')
  view.unmount()
})

it('shows clearing hints only when selected targets contain values to remove', async () => {
  const packages = [{...testPackage,id:'a'}, {...testPackage,id:'b',cover:'target.jpg',tags:['Materials']}]
  const view = await mount(<GroupSyncDialog session={{...session,data:{...session.data,packages}}} source="a" t={translator('en')} onClose={vi.fn()} onReload={vi.fn()} onApply={vi.fn()} />)
  expect(view.host.querySelector('.sync-field-grid')?.textContent).toContain('Removes target covers.')
  expect(view.host.querySelector('.sync-field-grid')?.textContent).toContain('Removes target tags.')
  await click([...view.host.querySelectorAll('button')].filter(b=>b.textContent==='Select none')[0])
  expect(view.host.querySelector('.sync-field-grid')?.textContent).not.toContain('Removes')
  expect(view.host.querySelectorAll('.sync-field-option input:checked')).toHaveLength(6)
  view.unmount()
})

it('keeps a failed cover thumbnail non-blocking and clears it on source change', async () => {
  vi.spyOn(api,'getCover').mockRejectedValue(new Error('Cannot read cover'))
  const packages = session.data.packages.map(p=>({...p,cover:p.id==='a'?'source.jpg':null}))
  const view = await mount(<GroupSyncDialog session={{...session,data:{...session.data,packages}}} source="a" t={translator('en')} onClose={vi.fn()} onReload={vi.fn()} onApply={vi.fn()} />)
  expect(view.host.querySelector('.sync-field-grid')?.textContent).toContain('Preview unavailable')
  expect(view.host.querySelector('[role="alert"]')).toBeNull()
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,180))})
  expect([...view.host.querySelectorAll('button')].find(b=>b.textContent?.includes('Review changes'))?.disabled).toBe(false)
  await click(view.host.querySelector('#sync-source')!)
  await click(view.host.querySelector('[role="option"]:last-child')!)
  expect(view.host.querySelector('.sync-field-grid')?.textContent).not.toContain('Preview unavailable')
  expect(view.host.querySelector('.sync-source-cover')).toBeNull()
  view.unmount()
})

it('loads only source covers and ignores an earlier source response', async () => {
  const first = deferred<string|null>(), second = deferred<string|null>()
  const cover = vi.spyOn(api,'getCover').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  const packages = session.data.packages.map(p=>({...p,cover:`${p.id}.jpg`}))
  const view = await mount(<GroupSyncDialog session={{...session,data:{...session.data,packages}}} source="a" t={translator('en')} onClose={vi.fn()} onReload={vi.fn()} onApply={vi.fn()} />)
  expect(cover).toHaveBeenCalledExactlyOnceWith('a')
  await click(view.host.querySelector('#sync-source')!)
  await click(view.host.querySelector('[role="option"]:last-child')!)
  await act(async()=>second.resolve('data:image/jpeg;base64,new'))
  expect(view.host.querySelector('.sync-source-cover')?.getAttribute('src')).toBe('data:image/jpeg;base64,new')
  await act(async()=>first.resolve('data:image/jpeg;base64,old'))
  expect(view.host.querySelector('.sync-source-cover')?.getAttribute('src')).toBe('data:image/jpeg;base64,new')
  expect(cover.mock.calls).toEqual([['a'],['b']])
  view.unmount()
})

it.each(['de','en'] as const)('labels review columns, distinguishes empty values and warns only for actual clearing (%s)', async language => {
  const before = {...testPackage,id:'b',name:'Target',sku:'',tags:['Materials'],cover:'target.jpg'}
  const after = {...before,tags:[],cover:null,sku:'123'}
  const longZip = `IM00000123-01_${'LongProductName'.repeat(25)}.zip`
  vi.mocked(api.previewGroupSync).mockResolvedValue({...preview,order:null,changes:[
    {before,after,fields:['sku','tags','cover'],partChanged:false,oldFilename:null,newFilename:longZip,issues:[]},
    {before:{...testPackage,id:'c'},after:{...testPackage,id:'c',part:'02'},fields:[],partChanged:true,oldFilename:'old.zip',newFilename:'new.zip',issues:[]},
  ]})
  const t = translator(language), apply=vi.fn()
  const view = await mount(<GroupSyncDialog session={session} source="a" t={t} onClose={vi.fn()} onReload={vi.fn()} onApply={apply} />)
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,180))})
  await click([...view.host.querySelectorAll('button')].find(b=>b.textContent?.includes(t('syncReview')))!)
  const cards = view.host.querySelectorAll('.sync-review-card')
  expect([...cards[0].querySelectorAll('thead th')].map(th=>th.textContent)).toEqual(language==='de'?['Feld','Bisher','Danach']:['Field','Before','After'])
  expect(cards[0].querySelector('.sync-review-file')?.textContent).toContain(language==='de'?'Noch nicht berechenbar':'Not yet calculable')
  expect(cards[0].querySelector('.sync-review-file')?.textContent).toContain(longZip)
  expect(cards[0].textContent).toContain(language==='de'?'Leer':'Empty')
  expect(cards[0].querySelector('.sync-warning')?.textContent).toContain('Tags, Cover')
  expect(cards[1].querySelector('.sync-warning')).toBeNull()
  expect(view.host.querySelector('.sync-note')?.textContent).toBe(t('syncZipHint'))
  expect(view.host.querySelector('.sync-note .icon')).not.toBeNull()
  expect([...view.host.querySelectorAll('button')].find(b=>b.textContent===t('syncApply'))?.disabled).toBe(false)
  expect(apply).not.toHaveBeenCalled()
  view.unmount()
})

it('defaults all fields and other targets, keeps part-only drafts until confirmation and preserves Back', async () => {
  const apply = vi.fn(), close = vi.fn()
  const view = await mount(<GroupSyncDialog session={session} source="a" t={translator('de')} onClose={close} onReload={vi.fn()} onApply={apply} />)
  expect(view.host.querySelectorAll('input:checked')).toHaveLength(8)
  const buttons = () => [...view.host.querySelectorAll('button')]
  await click(buttons().filter(b => b.textContent === 'Keine auswählen')[1])
  const rows = view.host.querySelectorAll<HTMLElement>('[data-sync-part]')
  await act(async () => rows[1].dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true})))
  expect(view.host.querySelector('[data-sync-part]')?.getAttribute('data-sync-part')).toBe('b')
  expect(apply).not.toHaveBeenCalled()
  await act(async () => { await new Promise(resolve => setTimeout(resolve,180)) })
  await click(buttons().find(b => b.textContent?.includes('Änderungen prüfen'))!)
  expect(api.previewGroupSync).toHaveBeenLastCalledWith(expect.objectContaining({fields:[],order:['b','a']}),session.context)
  await click(buttons().find(b => b.textContent === 'Zurück')!)
  expect(view.host.querySelector('[data-sync-part]')?.getAttribute('data-sync-part')).toBe('b')
  await click(buttons().find(b => b.textContent === 'Abbrechen')!)
  expect(close).toHaveBeenCalledOnce()
  expect(apply).not.toHaveBeenCalled()
  view.unmount()
})

it('resets targets on source change without resetting field selection and permits numbering to be disabled', async () => {
  const view = await mount(<GroupSyncDialog session={session} source="a" t={translator('en')} onClose={vi.fn()} onReload={vi.fn()} onApply={vi.fn()} />)
  await click([...view.host.querySelectorAll('button')].filter(b=>b.textContent==='Select none')[1])
  await click(view.host.querySelector('#sync-source')!)
  await click(view.host.querySelector('[role="option"]:last-child')!)
  expect(view.host.querySelector('.sync-choice')?.textContent).toContain('Source')
  expect(view.host.querySelectorAll('.sync-choice input:checked')).toHaveLength(1)
  expect(view.host.querySelectorAll('.sync-field-option input:checked')).toHaveLength(0)
  await click(view.host.querySelector('.sync-part-head input')!)
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,180))})
  expect(api.previewGroupSync).toHaveBeenLastCalledWith(expect.objectContaining({source:'b',targets:['a'],fields:[],order:null}),session.context)
  expect(view.host.querySelector('[data-sync-part]')?.getAttribute('tabindex')).toBe('-1')
  view.unmount()
})

it('scrolls the part list itself and Escape cancels a drag without closing the dialog', async () => {
  let animation: FrameRequestCallback = ()=>undefined
  vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>{animation=callback;return 1})
  vi.stubGlobal('cancelAnimationFrame',vi.fn())
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.hasPointerCapture = vi.fn(()=>false)
  const packages = Array.from({length:12},(_,i)=>({...testPackage,id:String(i),name:`Part ${i}`}))
  const close = vi.fn()
  const view = await mount(<GroupSyncDialog session={{...session,data:{...session.data,packages}}} source="0" t={translator('en')} onClose={close} onReload={vi.fn()} onApply={vi.fn()} />)
  const list = view.host.querySelector<HTMLElement>('.sync-part-preview')!, body=view.host.querySelector<HTMLElement>('.dialog-body')!
  vi.spyOn(list,'getBoundingClientRect').mockReturnValue(new DOMRect(0,0,500,300))
  vi.spyOn(body,'getBoundingClientRect').mockReturnValue(new DOMRect(0,0,500,800))
  const rows = [...view.host.querySelectorAll<HTMLElement>('[data-sync-part]')]
  rows.forEach((row,i)=>vi.spyOn(row,'getBoundingClientRect').mockReturnValue(new DOMRect(0,i*42,500,42)))
  await act(async()=>{
    rows[0].dispatchEvent(new MouseEvent('pointerdown',{bubbles:true,button:0,clientX:200,clientY:20}))
    rows[0].dispatchEvent(new MouseEvent('pointermove',{bubbles:true,buttons:1,clientX:200,clientY:290}))
    animation(0)
  })
  expect(list.scrollTop).toBe(9)
  expect(body.scrollTop).toBe(0)
  await act(async()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true})))
  expect(view.host.querySelector('.part-moving')).toBeNull()
  expect(view.host.querySelector('[data-sync-part]')?.getAttribute('data-sync-part')).toBe('0')
  expect(close).not.toHaveBeenCalled()
  view.unmount()
})

it('keeps a partial result visible and requires reload before another apply', async () => {
  const apply = vi.fn().mockResolvedValue({packages:[],unavailable:[{id:'a',error:{code:'ioError',params:{},details:null,causes:[]}}],worklist:session.worklist,error:{code:'syncReadbackFailed',params:{},details:null,causes:[]}})
  const close=vi.fn(), reload=vi.fn()
  const view = await mount(<GroupSyncDialog session={session} source="a" t={translator('en')} onClose={close} onReload={reload} onApply={apply}/>)
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,180))})
  const button=(text:string)=>[...view.host.querySelectorAll('button')].find(b=>b.textContent?.includes(text))!
  await click(button('Review changes'));await click(button('Apply'))
  expect(close).not.toHaveBeenCalled()
  expect((button('Apply') as HTMLButtonElement).disabled).toBe(true)
  expect(view.host.querySelector('[role="alert"]')?.textContent).toContain('read back')
  await click(button('Reload group'));expect(reload).toHaveBeenCalledOnce()
  view.unmount()
})
