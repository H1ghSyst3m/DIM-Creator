// @vitest-environment jsdom
import {act} from 'react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import type {DragDropEvent} from '@tauri-apps/api/webview'
import {api} from './api'
import {useNativeDrop} from './useNativeDrop'
import {mount,deferred} from './testSupport'

const native=vi.hoisted(()=>({listen:vi.fn()}))
vi.mock('@tauri-apps/api/webview',()=>({getCurrentWebview:()=>({onDragDropEvent:native.listen})}))
vi.mock('@tauri-apps/api/core',async original=>({...await original<typeof import('@tauri-apps/api/core')>(),isTauri:()=>true}))
beforeEach(()=>{vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})

it('forwards hover, drop and leave with CSS coordinates, preserving all paths',async()=>{
 let deliver!:(value:{payload:DragDropEvent})=>void
 const unlisten=vi.fn()
 native.listen.mockImplementation(async(callback:typeof deliver)=>{deliver=callback;return unlisten})
 vi.stubGlobal('devicePixelRatio',2)
 const receive=vi.fn()
 const stop=await api.onDrop(receive)
 deliver({payload:{type:'enter',paths:['A.ZIP','B.7z'],position:{x:200,y:100}} as DragDropEvent})
 deliver({payload:{type:'over',position:{x:220,y:120}} as DragDropEvent})
 deliver({payload:{type:'drop',paths:['A.ZIP','notes.txt'],position:{x:240,y:140}} as DragDropEvent})
 deliver({payload:{type:'leave'}})
 expect(receive.mock.calls.map(([event])=>event)).toEqual([
   {type:'enter',paths:['A.ZIP','B.7z'],x:100,y:50},
   {type:'over',x:110,y:60},
   {type:'drop',paths:['A.ZIP','notes.txt'],x:120,y:70},
   {type:'leave'},
 ])
 stop();expect(unlisten).toHaveBeenCalledOnce()
})

it('routes archive drops to import zones, keeps mixed file copies intact and clears hover',async()=>{
 let deliver!:(value:{payload:DragDropEvent})=>void
 const stop=vi.fn()
 native.listen.mockImplementation(async(callback:typeof deliver)=>{deliver=callback;return stop})
 const imported=vi.fn(),copied=vi.fn(),cover=vi.fn(),error=vi.fn()
 function Harness({blocked=false,id=undefined}:{blocked?:boolean;id?:string}){
   const state=useNativeDrop({blocked,packageId:id,folder:'People',onImport:imported,onCopy:copied,onCover:cover,onError:error})
   return <><div data-drop-target="worklist"/><div data-drop-target="files"><span data-drop-folder="Runtime"/></div><div data-drop-target="archive"/><div data-drop-target="cover"/><output>{JSON.stringify(state)}</output></>
 }
 const view=await mount(<Harness/>)
 let area=view.host.querySelector('[data-drop-target="worklist"]')!
 Object.defineProperty(document,'elementFromPoint',{configurable:true,value:()=>area})
 const emit=async(type:'enter'|'over'|'drop'|'leave',paths=['A.zip','B.7Z'])=>act(async()=>{deliver({payload:{type,paths,position:{x:30,y:40}} as DragDropEvent})})
 const state=()=>JSON.parse(view.host.querySelector('output')!.textContent!)
 await emit('enter');expect(state().preview).toMatchObject({area:'worklist',count:2,valid:true})
 await emit('drop');expect(imported).toHaveBeenLastCalledWith(['A.zip','B.7Z'],undefined)
 expect(state().preview).toBeNull()
 await emit('enter',['A.zip','readme.txt']);expect(state().preview.valid).toBe(false)
 await emit('drop',['A.zip','readme.txt']);expect(imported).toHaveBeenCalledTimes(1)
 expect(error).toHaveBeenLastCalledWith(expect.objectContaining({code:'archiveDropOnly'}))
 await view.render(<Harness id="package"/>)
 area=view.host.querySelector('[data-drop-target="archive"]')!
 await emit('enter');await emit('drop');expect(imported).toHaveBeenLastCalledWith(['A.zip','B.7Z'],'package')
 area=view.host.querySelector('[data-drop-folder]')!
 await emit('enter',['A.zip','readme.txt']);expect(state().preview).toMatchObject({area:'files',folder:'Runtime',valid:true})
 await emit('drop',['A.zip','readme.txt']);expect(copied).toHaveBeenCalledWith(['A.zip','readme.txt'],'Runtime')
 area=view.host.querySelector('[data-drop-target="cover"]')!
 await emit('drop',['cover.png']);expect(cover).toHaveBeenCalledWith('cover.png')
 await emit('enter');await emit('leave');expect(state()).toEqual({archives:false,preview:null})
 await emit('enter');await act(async()=>{window.dispatchEvent(new Event('blur'))});await emit('over');expect(state().preview).toBeNull()
 await emit('enter');await act(async()=>{window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))});expect(state().preview).toBeNull()
 await emit('enter');await view.render(<Harness blocked id="package"/>);expect(state().archives).toBe(false)
 await emit('drop');expect(cover).toHaveBeenCalledTimes(1)
 view.unmount();expect(stop).toHaveBeenCalledOnce()
})

it('unregisters a late subscription without delivering events after unmount',async()=>{
 let deliver!:(value:{payload:DragDropEvent})=>void
 const pending=deferred<()=>void>(),error=vi.fn(),imported=vi.fn()
 native.listen.mockImplementation((callback:typeof deliver)=>{deliver=callback;return pending.promise})
 function Harness(){useNativeDrop({blocked:false,folder:'',onImport:imported,onCopy:vi.fn(),onCover:vi.fn(),onError:error});return null}
 const view=await mount(<Harness/>);view.unmount()
 const stop=vi.fn()
 await act(async()=>{pending.resolve(stop)})
 deliver({payload:{type:'drop',paths:['a.zip'],position:{x:0,y:0}} as DragDropEvent})
 expect(stop).toHaveBeenCalledOnce();expect(imported).not.toHaveBeenCalled();expect(error).not.toHaveBeenCalled()
})
