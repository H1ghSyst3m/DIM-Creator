// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Worklist } from './Worklist'
import { canNudge } from './worklistView'
import type { WorklistAction, WorklistState } from './api'
import { filterPackages, markVisible } from './workflow'
import { translator } from './messages'
import { click, input, mount, testPackage } from './testSupport'

const packages = ['Apple','Banana','Cherry','Date','Elderberry'].map((name,index) => ({...testPackage,id:String(index+1),name}))
const initial: WorklistState = {revision:1,canUndo:true,saveError:null,warnings:[],entries:[
  {kind:'group',id:'g',members:['1','2'],collapsed:true}, {kind:'package',id:'3'}, {kind:'group',id:'h',members:['4','5'],collapsed:false},
]}
let dispose: (() => void) | undefined
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
  vi.stubGlobal('ResizeObserver',class {observe(){} disconnect(){}})
  vi.stubGlobal('PointerEvent',MouseEvent)
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => false)
})
afterEach(() => {dispose?.(); dispose=undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

async function setup(state = initial) {
  const build = vi.fn(), remove = vi.fn(), structure = vi.fn<(action?:WorklistAction)=>Promise<boolean>>().mockResolvedValue(false)
  let selected!: ReadonlySet<string>, active!: string
  function Host() {
    const [selection,setSelection] = useState<ReadonlySet<string>>(new Set())
    const [opened,setOpened] = useState('3'), [query,setQuery] = useState('')
    selected=selection;active=opened
    const visible=filterPackages(packages,query)
    return <Worklist packages={packages} visible={visible} worklist={state} active={opened} selected={selection} previews={{}} query={query} language="en" t={translator('en')} disabled={false}
      onQuery={setQuery} onActive={setOpened} onCheck={(id,checked) => setSelection(markVisible(selection,[{id}],checked))} onCheckAll={checked => setSelection(markVisible(selection,visible,checked))}
      onSelection={setSelection} onClear={() => setSelection(new Set())} onAdd={() => {}} onValidate={() => {}} onBuild={build} onRemove={remove} onStructure={structure} />
  }
  const rendered=await mount(<Host/>);dispose=rendered.unmount
  const row=(id:string)=>rendered.host.querySelector<HTMLElement>(`[data-package-id="${id}"]`)!
  const link=(id:string)=>row(id).querySelector<HTMLButtonElement>('.package-link')!
  const key=async (element:Element,key:string,options:KeyboardEventInit={})=>act(async()=>{element.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key,...options}))})
  const context=async (element:Element)=>act(async()=>{element.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:40,clientY:60}))})
  return {...rendered,row,link,key,context,build,remove,structure,selected:()=>[...selected],active:()=>active,search:()=>rendered.host.querySelector<HTMLInputElement>('input[type="search"]')!}
}

it('selects collapsed members, preserves hidden marks, and temporarily expands search matches',async()=>{
  const ui=await setup()
  expect(ui.row('1')).toBeNull()
  await click(ui.host.querySelector<HTMLInputElement>('.select-all input')!)
  expect(ui.selected()).toEqual(['1','2','3','4','5'])
  await input(ui.search(),'apple')
  expect(ui.row('1')).not.toBeNull();expect(ui.row('2')).toBeNull()
  const groupCheck=ui.host.querySelector<HTMLInputElement>('.group-gutter input')!
  await click(groupCheck)
  expect(ui.selected()).toEqual(['2','3','4','5'])
  await input(ui.search(),'')
  expect(ui.row('1')).toBeNull();expect(ui.active()).toBe('3')
  expect(ui.host.querySelector<HTMLInputElement>('.group-gutter input')!.indeterminate).toBe(true)
  expect(ui.structure).not.toHaveBeenCalled()
})

it('keeps opening and batch selection separate and includes collapsed IDs in a range',async()=>{
  const ui=await setup()
  await click(ui.link('3'))
  await act(async()=>{ui.link('5').dispatchEvent(new MouseEvent('click',{bubbles:true,shiftKey:true}))})
  expect(ui.selected()).toEqual(['3','4','5']);expect(ui.active()).toBe('3')
  await input(ui.search(),'apple');await click(ui.link('1'));await input(ui.search(),'')
  await act(async()=>{ui.link('4').dispatchEvent(new MouseEvent('click',{bubbles:true,shiftKey:true}))})
  expect(ui.selected()).toEqual(['4'])
  await ui.key(ui.host.querySelector('.package-list')!,'a',{ctrlKey:true})
  expect(new Set(ui.selected())).toEqual(new Set(['1','2','3','4','5']))
  await ui.key(ui.search(),'Delete');expect(ui.remove).not.toHaveBeenCalled()
  await ui.key(ui.host.querySelector('.package-list')!,'Delete');expect(ui.remove).toHaveBeenCalledWith(['1','2','3','4','5'])
})

it('freezes row and group menu targets without changing selection or the opened package',async()=>{
  const ui=await setup()
  await click(ui.row('4').querySelector('input')!);await click(ui.row('5').querySelector('input')!)
  await ui.context(ui.row('4'))
  await click([...document.querySelectorAll('[role="menuitem"]')].find(el=>el.textContent?.includes('Build selection'))!)
  expect(ui.build).toHaveBeenLastCalledWith(['4','5'])
  await ui.context(ui.row('3'))
  await click([...document.querySelectorAll('[role="menuitem"]')].find(el=>el.textContent?.includes('Build selection'))!)
  expect(ui.build).toHaveBeenLastCalledWith(['3']);expect(ui.selected()).toEqual(['4','5']);expect(ui.active()).toBe('3')
  await ui.key(ui.host.querySelector('[data-group-id="g"] .group-toggle')!,'F10',{shiftKey:true})
  await click([...document.querySelectorAll('[role="menuitem"]')].find(el=>el.textContent?.includes('Build group'))!)
  expect(ui.build).toHaveBeenLastCalledWith(['1','2'])
})

it('limits keyboard reorder and undo to the worklist and valid parent scopes',async()=>{
  const ui=await setup()
  await ui.key(ui.link('5'),'ArrowUp',{altKey:true})
  expect(ui.structure).toHaveBeenLastCalledWith({kind:'nudge',source:{kind:'packages',ids:['5']},up:true})
  await ui.key(ui.search(),'z',{ctrlKey:true});expect(ui.structure).toHaveBeenCalledTimes(1)
  await ui.key(ui.link('3'),'z',{ctrlKey:true});expect(ui.structure).toHaveBeenLastCalledWith(undefined)
  await input(ui.search(),'cherry');await ui.key(ui.link('3'),'ArrowUp',{altKey:true});expect(ui.structure).toHaveBeenCalledTimes(2)
  expect(canNudge(initial.entries,{kind:'packages',ids:['2','3']},true)).toBe(false)
  expect(canNudge(initial.entries,{kind:'group',id:'h'},true)).toBe(true)
})

it('starts dragging only after six pixels, shows hidden selection count, and cancels with Escape',async()=>{
  const ui=await setup()
  await click(ui.host.querySelector('.select-all input')!)
  const button=ui.link('3')
  await act(async()=>{button.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true,button:0,clientX:40,clientY:100}))})
  await act(async()=>{window.dispatchEvent(new MouseEvent('pointermove',{clientX:43,clientY:102}))})
  expect(document.querySelector('.worklist-drag-ghost')).toBeNull()
  await act(async()=>{window.dispatchEvent(new MouseEvent('pointermove',{clientX:47,clientY:102}))})
  expect(document.querySelector('.worklist-drag-ghost')?.textContent).toContain('5 packages')
  await ui.key(button,'Escape')
  expect(document.querySelector('.worklist-drag-ghost')).toBeNull()
  await act(async()=>{window.dispatchEvent(new MouseEvent('pointerup',{clientX:47,clientY:102}))})
  expect(ui.structure).not.toHaveBeenCalled()
})


it('anchors keyboard group actions to their button and restores the enabled search control', async()=>{
  const ui=await setup()
  const more=ui.host.querySelector<HTMLButtonElement>('.group-more')!
  vi.spyOn(more,'getBoundingClientRect').mockReturnValue({left:100,right:123,top:200,bottom:223,width:23,height:23,x:100,y:200,toJSON(){}})
  await click(more)
  expect(document.querySelector<HTMLElement>('[role="menu"]')!.style.left).toBe('100px')
  await ui.key(document.querySelector('[role="menuitem"]')!,'Escape')
  expect(document.activeElement).toBe(more)
  await click(more)
  await click([...document.querySelectorAll('[role="menuitem"]')].find(el=>el.textContent?.includes('Select all packages in group'))!)
  expect(document.activeElement).toBe(more)
  await input(ui.search(),'apple')
  const checkbox=ui.host.querySelector<HTMLInputElement>('.group-gutter input')!
  checkbox.focus();await ui.key(checkbox,'F10',{shiftKey:true})
  await ui.key(document.querySelector('[role="menuitem"]:not(:disabled)')!,'Escape')
  expect(document.activeElement).toBe(checkbox)
})

it('distinguishes row centers, gutter, and outside root lines using actual hit rectangles', async()=>{
  const state: WorklistState={...initial,entries:[{kind:'group',id:'g',members:['1','2','3'],collapsed:false},{kind:'package',id:'4'},{kind:'package',id:'5'}]}
  const ui=await setup(state)
  const rect=(x:number,y:number,width:number,height:number)=>({x,y,width,height,left:x,right:x+width,top:y,bottom:y+height,toJSON(){}})
  vi.spyOn(ui.host.querySelector('.package-list')!,'getBoundingClientRect').mockReturnValue(rect(0,0,370,600))
  vi.spyOn(ui.host.querySelector('[data-group-id="g"]')!,'getBoundingClientRect').mockReturnValue(rect(10,20,340,230))
  for(const [i,id] of ['1','2','3'].entries())vi.spyOn(ui.row(id),'getBoundingClientRect').mockReturnValue(rect(42,25+i*72,300,72))
  vi.spyOn(ui.row('4'),'getBoundingClientRect').mockReturnValue(rect(32,260,318,65))
  vi.spyOn(ui.row('5'),'getBoundingClientRect').mockReturnValue(rect(32,328,318,65))
  const move=async(id:string,x:number,y:number)=>{
    await act(async()=>{ui.link(id).dispatchEvent(new MouseEvent('pointerdown',{bubbles:true,button:0,clientX:100,clientY:200}))})
    await act(async()=>{window.dispatchEvent(new MouseEvent('pointermove',{clientX:x,clientY:y}))})
  }
  await move('3',100,65)
  expect(document.querySelector('.worklist-drag-ghost')?.textContent).toContain('Reorder within group')
  await act(async()=>{window.dispatchEvent(new MouseEvent('pointerup',{clientX:100,clientY:65}))})
  expect(ui.structure).toHaveBeenLastCalledWith({kind:'move',source:{kind:'packages',ids:['3']},target:{kind:'member',group:'g',anchor:'1',before:false}})
  await move('4',5,130)
  expect(document.querySelector('.worklist-drag-ghost')?.textContent).toContain('Place independently')
  await ui.key(ui.link('4'),'Escape')
  await move('4',25,28)
  expect(document.querySelector('.worklist-drag-ghost')?.textContent).toContain('Place independently')
  await ui.key(ui.link('4'),'Escape')
  await move('4',25,100)
  expect(document.querySelector('.worklist-drag-ghost')?.textContent).toContain('Add to group')
  expect(document.querySelector('.worklist-drop-line')).toBeNull()
  await ui.key(ui.link('4'),'Escape')
})
