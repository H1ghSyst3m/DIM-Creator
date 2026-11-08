// @vitest-environment jsdom
import {act} from 'react'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {App} from './App'
import {api,type NativeDropEvent,type WorklistState} from './api'
import {mount,click,input,testPackage,testSettings,deferred} from './testSupport'

vi.mock('@tauri-apps/api/core',async original=>({...await original<typeof import('@tauri-apps/api/core')>(),isTauri:()=>true}))
vi.mock('@tauri-apps/api/window',()=>({getCurrentWindow:()=>({onCloseRequested:async()=>()=>{}})}))
const worklist:WorklistState={revision:1,canUndo:false,saveError:null,warnings:[],entries:[{kind:'package',id:testPackage.id}]}
const session={id:'session',templateDirectoryIssue:null,contents:[{id:'one',archiveId:'one',classification:'content' as const,suggestedAction:'package' as const,templateIssue:null,templatePath:null,archiveName:'A.zip',origin:[],roots:[{id:'root',path:'',folders:['People'],files:1,bytes:5}],suggestedGroup:null,issue:null}]}
const progress={phase:'starting' as const,name:'',entries:0,bytes:0}
let deliver:(event:NativeDropEvent)=>void,dispose:()=>void
beforeEach(()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}})
 HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false};HTMLElement.prototype.scrollIntoView=vi.fn()
 vi.spyOn(api,'loadWorkspace').mockResolvedValue({packages:[testPackage],settings:{...testSettings,language:'en'},warnings:[],worklist})
 vi.spyOn(api,'previewOutputs').mockResolvedValue([]);vi.spyOn(api,'preview').mockResolvedValue({filename:'test.zip',size:0,entries:[],issues:[]});vi.spyOn(api,'listFiles').mockResolvedValue([])
 vi.spyOn(api,'onDrop').mockImplementation(async callback=>{deliver=callback;return()=>{}})
 vi.spyOn(api,'analyzeArchives').mockImplementation(async(_sources,receive)=>{receive({jobId:'analysis',progress,finished:true,session,result:null,error:null});return'analysis'})
 vi.spyOn(api,'releaseArchiveSession').mockResolvedValue()
})
afterEach(()=>{dispose?.();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('accepts worklist archives without a package and rejects mixed selections as a whole',async()=>{
 vi.mocked(api.loadWorkspace).mockResolvedValue({packages:[],settings:testSettings,warnings:[],worklist:{...worklist,entries:[]}})
 const ui=await mount(<App/>);dispose=ui.unmount
 Object.defineProperty(document,'elementFromPoint',{configurable:true,value:()=>ui.host.querySelector('.worklist')})
 await act(async()=>deliver({type:'drop',paths:['A.zip','note.txt'],x:5,y:5}))
 expect(api.analyzeArchives).not.toHaveBeenCalled()
 await act(async()=>deliver({type:'drop',paths:['A.zip','B.RAR'],x:5,y:5}))
 expect(api.analyzeArchives).toHaveBeenCalledWith(['A.zip','B.RAR'],expect.any(Function))
 expect(ui.host.querySelector('dialog')).not.toBeNull()
 expect(ui.host.querySelectorAll('.archive-target input')).toHaveLength(1)
})

it('flushes edits before import and returns focus to the worklist without changing selection or the active package',async()=>{
 const writing=deferred<void>(),save=vi.spyOn(api,'savePackage').mockReturnValue(writing.promise)
 const result={templates:[],packages:[{...testPackage,name:'Edited'}],worklist,results:[{targetId:'target',packageId:testPackage.id,status:'success' as const,importedFiles:1,skippedFiles:0,error:null}]}
 const preview={id:'plan',templates:[],targets:[{id:'target',package:{...testPackage,name:'Edited'},existing:true,sources:['A.zip'],files:1,bytes:5,conflicts:[]}],groups:[],skipped:0}
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 vi.spyOn(api,'startArchiveImport').mockImplementation(async(_session,_plan,_policy,receive)=>{receive({jobId:'import',progress,finished:true,session:null,result,error:null});return'import'})
 const ui=await mount(<App/>);dispose=ui.unmount
 await click(ui.host.querySelector('.select-all input')!)
 await input(ui.host.querySelector('#field-name')!,'Edited')
 Object.defineProperty(document,'elementFromPoint',{configurable:true,value:()=>ui.host.querySelector('[data-drop-target="archive"]')})
 await act(async()=>deliver({type:'enter',paths:['A.zip'],x:5,y:5}))
 expect(ui.host.querySelector('[data-drop-target="archive"]')).not.toBeNull()
 expect(ui.host.querySelector('.native-drop-label')?.textContent).toContain('Import 1 archive → Edited / Content')
 await act(async()=>deliver({type:'drop',paths:['A.zip'],x:5,y:5}))
 expect(save).toHaveBeenCalledOnce();expect(api.analyzeArchives).not.toHaveBeenCalled()
 await act(async()=>writing.resolve())
 expect(api.analyzeArchives).toHaveBeenCalledOnce()
 expect(ui.host.querySelector('.archive-target')?.textContent).toContain('Edited')
 expect(ui.host.querySelector('.archive-target input')).toBeNull()
 await click([...ui.host.querySelectorAll('button')].find(b=>b.textContent==='Review import')!)
 await click(ui.host.querySelector('.dialog-footer .primary')!)
 expect(api.startArchiveImport).toHaveBeenCalledWith('session','plan','cancel',expect.any(Function))
 await click([...ui.host.querySelectorAll('button')].find(b=>b.textContent==='Back to worklist')!)
 expect(ui.host.querySelector('dialog')).toBeNull()
 expect(document.activeElement).toBe(ui.host.querySelector('.package-list'))
 expect(ui.host.querySelector('.main-grid')?.getAttribute('data-panel')).toBe('list')
 expect(ui.host.querySelector<HTMLInputElement>('#field-name')!.value).toBe('Edited')
 expect(ui.host.querySelector<HTMLInputElement>('.package-row input')!.checked).toBe(true)
})
