// @vitest-environment jsdom
import {act} from 'react'
import {beforeEach,afterEach,expect,it,vi} from 'vitest'
import {ArchiveImportDialog} from './ArchiveImportDialog'
import {api,type AppError,type ArchiveSessionView,type FileStatus,type ImportPreview} from './api'
import {translator} from './messages'
import {mount,click,input,testPackage} from './testSupport'
import type {useArchiveImport} from './useArchiveImport'

const session:ArchiveSessionView={id:'session',templateDirectoryIssue:null,contents:['One.zip','Two.7z'].map((archiveName,index)=>({id:String(index),archiveId:String(index),classification:'content' as const,suggestedAction:'package' as const,templateIssue:null,templatePath:null,archiveName,origin:['Bundle.rar'],roots:[{id:'root',path:'Content',folders:['People'],files:2,bytes:8}],suggestedGroup:'group',issue:null}))}
const imports=()=>({session,running:false,cancelling:false,cancel:vi.fn(),close:vi.fn(),analyze:vi.fn(),start:vi.fn(),isRunning:()=>false}) as unknown as ReturnType<typeof useArchiveImport>
beforeEach(()=>{vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});HTMLElement.prototype.scrollIntoView=vi.fn();HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false}})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})

it('switches all units of an archive to one template and restores names without reviving groups',async()=>{
 const data:ArchiveSessionView={...session,contents:session.contents.map(item=>({...item,archiveId:'shared',archiveName:'Collection.zip',templatePath:'D:/Templates/Collection.zip'}))}
 const view=await mount(<ArchiveImportDialog imports={{...imports(),session:data}} packages={[]} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/>)
 const names=()=>view.host.querySelectorAll<HTMLInputElement>('.archive-target input')
 await input(names()[0],'Remember me')
 await click(view.host.querySelector('#archive-target-0')!)
 const template=[...view.host.querySelectorAll('[role="option"]')].find(option=>option.textContent==='Save as template')
 expect(template).toBeDefined()
 await click(template!)
 expect(view.host.querySelectorAll('.archive-row')).toHaveLength(1)
 expect(names()).toHaveLength(0)
 expect(view.host.textContent).not.toContain('D:/Templates')
 expect(view.host.querySelectorAll('.archive-group')).toHaveLength(0)
 await click(view.host.querySelector('#archive-target-0')!)
 await click([...view.host.querySelectorAll('[role="option"]')].find(option=>option.textContent==='Exclude')!)
 expect(view.host.querySelectorAll('input[type="checkbox"]:checked')).toHaveLength(0)
 await click(view.host.querySelector('#archive-target-0')!)
 await click([...view.host.querySelectorAll('[role="option"]')].find(option=>option.textContent==='New package')!)
 expect(names()[0].value).toBe('Remember me')
 expect(view.host.querySelectorAll('.archive-group')).toHaveLength(0)
 view.unmount()
})

it('reviews a template-only import and reports saved and skipped templates without worklist data',async()=>{
 const data:ArchiveSessionView={...session,contents:session.contents.map(item=>({...item,roots:[],classification:'template',suggestedAction:'template',templatePath:`D:/Templates/${item.archiveName}`}))}
 const preview:ImportPreview={id:'plan',targets:[],groups:[],skipped:0,templates:data.contents.map((item,index)=>({archiveId:item.archiveId,name:item.archiveName,origin:['Bundle.rar'],path:item.templatePath!,sourceLocation:`D:/Downloads/${item.archiveName}`,bytes:8,conflicts:index?['existing']:[],existingPath:index?item.templatePath!:null,conflictingArchives:[],saveOnReplace:true,saveOnSkip:index===0}))}
 const check=vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview),start=vi.fn()
 const state={...imports(),session:data},props={packages:[],t:translator('en'),language:'en',onClose:vi.fn(),onFinish:vi.fn(),onStart:start}
 const view=await mount(<ArchiveImportDialog {...props} imports={state}/>)
 const primary=()=>view.host.querySelector<HTMLButtonElement>('.dialog-footer .primary')!
 expect(primary().disabled).toBe(false)
 await click(primary())
 expect(check).toHaveBeenCalledOnce()
 expect(view.host.querySelectorAll('.archive-template-card')).toHaveLength(2)
 const folder=view.host.querySelector<HTMLInputElement>('.archive-template-directory input')!
 expect(folder.value).toBe('D:/Templates/')
 expect(folder.readOnly).toBe(true)
 expect(folder.getAttribute('aria-label')).toBe('Template folder')
 expect(view.host.querySelectorAll('.archive-template-directory')).toHaveLength(1)
 expect(view.host.querySelector('.archive-template-card')!.textContent).not.toContain('D:/Templates')
 expect(view.host.querySelector('.archive-summary')!.textContent).not.toContain('0 packages')
 expect(primary().disabled).toBe(true)
 await click(view.host.querySelectorAll('.archive-conflict-options input')[1])
 expect(view.host.textContent).toContain('Existing file will be kept.')
 await click(primary())
 expect(start).toHaveBeenCalledWith(preview,'skip')
 await view.render(<ArchiveImportDialog {...props} imports={{...state,session:undefined,result:{packages:[],results:[],worklist:null,templates:preview.templates.map((item,index)=>({archiveId:item.archiveId,path:item.path,status:index?'skipped':'success',reason:index?'existing':null,error:null}))}}}/>)
 expect(view.host.querySelector('.archive-result-summary')!.textContent).toContain('1 template saved')
 expect(view.host.querySelector('.archive-result-summary strong')!.textContent).toBe('Import complete')
 expect(view.host.querySelector('.archive-results .warning,.archive-results .error')).toBeNull()
 expect(view.host.querySelector('.archive-result-summary')!.textContent).not.toContain('0 ')
 expect(view.host.querySelector('.archive-results')!.textContent).not.toContain('D:/Templates')
 expect(view.host.textContent).toContain('Skipped · existing file kept')
 view.unmount()
})

it('retargets a whole source to the same package without starting a drag from the popup',async()=>{
 const check=vi.spyOn(api,'previewArchiveImport').mockResolvedValue({id:'plan',templates:[],targets:[],groups:[],skipped:0})
 const view=await mount(<ArchiveImportDialog imports={imports()} packages={[]} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/> )
 await click(view.host.querySelector('#archive-target-1')!)
 const option=[...view.host.querySelectorAll('[role="option"]')].find(item=>item.textContent?.includes('One'))!
 const capture=vi.fn();HTMLElement.prototype.setPointerCapture=capture
 await act(async()=>{const event=new Event('pointerdown',{bubbles:true});Object.defineProperty(event,'button',{value:0});option.dispatchEvent(event)})
 expect(capture).not.toHaveBeenCalled()
 await click(option)
 expect([...view.host.querySelectorAll('.archive-target input')].map(input=>(input as HTMLInputElement).value)).toEqual(['One'])
 expect(view.host.querySelector('[data-archive-row="1"]')?.textContent).toContain('Shared target')
 await input(view.host.querySelector('.archive-target input')!, 'Shared product')
 expect(view.host.querySelector('[data-archive-row="1"]')?.textContent).toContain('Shared product')
 await click(view.host.querySelector('[data-archive-row="0"] input[type="checkbox"]')!)
 expect(view.host.querySelector('[data-archive-row="1"] .archive-target input')).not.toBeNull()
 expect(view.host.querySelectorAll('.archive-target input')).toHaveLength(1)
 expect(view.host.querySelector('.archive-count')?.textContent).toContain('1 target')
 await click([...view.host.querySelectorAll('button')].find(b=>b.textContent==='Review import')!)
 const draft=check.mock.calls[0][1]
 expect(draft.assignments[0].targetId).toBe(draft.assignments[1].targetId)
 expect(draft.groups).toEqual([])
 view.unmount()
})

it.each(['de','en'])('shows one actionable missing-folder message in %s and clears it on exclusion',async language=>{
 const t=translator(language)
 const data:ArchiveSessionView={...session,templateDirectoryIssue:{code:'templateDirectoryRequired',params:{},details:null,causes:[]},contents:session.contents.map(item=>({...item,roots:[],classification:'template',suggestedAction:'template'}))}
 const view=await mount(<ArchiveImportDialog imports={{...imports(),session:data}} packages={[]} t={t} language={language} onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/>)
 expect(view.host.querySelectorAll('[role="alert"]')).toHaveLength(1)
 expect(view.host.querySelector('[role="alert"]')!.textContent).toContain(t('templateDirectoryRequired'))
 expect(view.host.querySelector<HTMLButtonElement>('.dialog-footer .primary')!.disabled).toBe(true)
 await click([...view.host.querySelectorAll('button')].find(button=>button.textContent===t('syncSelectNone'))!)
 expect(view.host.querySelector('[role="alert"]')).toBeNull()
 view.unmount()
})

it.each(['de','en'])('shows template progress and keeps cancellation separate from conflict decisions in %s',async language=>{
 const t=translator(language),cancel=vi.fn(),state={...imports(),running:true,cancel,event:{jobId:'job',finished:false,session:null,result:null,error:null,progress:{phase:'template' as const,name:'Long Template.zip',entries:1,bytes:2048}}}
 const props={packages:[],t,language,onClose:vi.fn(),onFinish:vi.fn(),onStart:vi.fn()}
 const view=await mount(<ArchiveImportDialog {...props} imports={state}/>)
 expect(view.host.querySelector('[role="status"]')!.textContent).toContain(t('templateSaving'))
 expect(view.host.querySelector('.archive-conflict-options')).toBeNull()
 await click([...view.host.querySelectorAll('button')].find(button=>button.textContent===t('cancel'))!)
 expect(cancel).toHaveBeenCalledOnce()
 await view.render(<ArchiveImportDialog {...props} imports={{...state,cancelling:true}}/>)
 expect(view.host.querySelector<HTMLButtonElement>('.dialog-footer button')!.disabled).toBe(true)
 expect(view.host.querySelector('.dialog-footer')!.textContent).toContain(t('archiveCancelling'))
 view.unmount()
})

it.each(['E:\\','E:/','/','\\\\server\\share\\'])('preserves the complete root folder %s in review',async directory=>{
 const preview:ImportPreview={id:'plan',targets:[],groups:[],skipped:0,templates:[{archiveId:'0',name:'One.zip',origin:[],path:directory+'One.zip',sourceLocation:'D:/Downloads/One.zip',bytes:8,conflicts:[],existingPath:null,conflictingArchives:[],saveOnReplace:true,saveOnSkip:true}]}
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 const view=await mount(<ArchiveImportDialog imports={imports()} packages={[]} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/>)
 await click(view.host.querySelector('.dialog-footer .primary')!)
 expect(view.host.querySelector<HTMLInputElement>('.archive-template-directory input')!.value).toBe(directory)
 view.unmount()
})

it('identifies colliding archives and the selected winner without inferring native path equality',async()=>{
 const data:ArchiveSessionView={...session,contents:session.contents.map((item,index)=>({...item,archiveName:index?'shared.zip':'Shared.zip',origin:[index?'Second.rar':'First.rar'],roots:[],classification:'template',suggestedAction:'template'}))}
 const preview:ImportPreview={id:'plan',targets:[],groups:[],skipped:0,templates:data.contents.map((item,index)=>({archiveId:item.archiveId,name:item.archiveName,origin:item.origin,path:`D:/Templates/${item.archiveName}`,sourceLocation:`D:/Downloads/${index}/${item.archiveName}`,bytes:8,conflicts:[item.archiveName],existingPath:null,conflictingArchives:[String(1-index)],saveOnReplace:!!index,saveOnSkip:!index}))}
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 const view=await mount(<ArchiveImportDialog imports={{...imports(),session:data}} packages={[]} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/>)
 await click(view.host.querySelector('.dialog-footer .primary')!)
 const cards=view.host.querySelectorAll('.archive-template-card')
 expect(cards[0].textContent).toContain('Second.rar › shared.zip')
 await click(view.host.querySelectorAll('.archive-conflict-options input')[0])
 expect(cards[0].textContent).toContain('Will be skipped; “Second.rar › shared.zip” will be saved.')
 expect(cards[1].textContent).toContain('This archive will be saved.')
 await click(view.host.querySelectorAll('.archive-conflict-options input')[1])
 expect(cards[1].textContent).toContain('Will be skipped; “First.rar › Shared.zip” will be saved.')
 view.unmount()
})

it('distinguishes identical direct archive names in review and results, including a failed winner',async()=>{
 const templates=['D:/A/Shared.zip','D:/B/Shared.zip'].map((sourceLocation,index)=>({archiveId:String(index),name:'Shared.zip',origin:[],sourceLocation,path:'D:/Templates/Shared.zip',bytes:8,conflicts:['Shared.zip'],existingPath:null,conflictingArchives:[String(1-index)],saveOnReplace:!!index,saveOnSkip:!index}))
 const preview:ImportPreview={id:'plan',targets:[],groups:[],skipped:0,templates}
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 const state=imports(),props={packages:[],t:translator('en'),language:'en',onClose:vi.fn(),onFinish:vi.fn(),onStart:vi.fn()}
 const view=await mount(<ArchiveImportDialog {...props} imports={state}/>)
 await click(view.host.querySelector('.dialog-footer .primary')!)
 expect([...view.host.querySelectorAll<HTMLInputElement>('.archive-origin-path')].map(input=>input.value)).toEqual(['D:/A/Shared.zip','D:/B/Shared.zip'])
 await click(view.host.querySelector('.archive-conflict-options input')!)
 expect(view.host.querySelector('.archive-template-card')!.textContent).toContain('“Archive 2: Shared.zip” will be saved.')
 await view.render(<ArchiveImportDialog {...props} imports={{...state,session:undefined,result:{packages:[],results:[],worklist:null,templates:[{archiveId:'0',path:templates[0].path,status:'skipped',reason:'collision',error:null},{archiveId:'1',path:templates[1].path,status:'failed',reason:null,error:{code:'ioError',params:{},details:null,causes:[]}}]}}}/>)
 expect(view.host.querySelector('.archive-result-summary strong')!.textContent).toBe('Review results')
 expect(view.host.querySelector('.archive-result.muted')!.textContent).toContain('Skipped · another archive selected')
 expect(view.host.querySelector('.archive-result.error')!.textContent).toContain('Archive 2: Shared.zip')
 expect([...view.host.querySelectorAll<HTMLInputElement>('.archive-origin-path')].map(input=>input.value)).toEqual(['D:/A/Shared.zip','D:/B/Shared.zip'])
 view.unmount()
})

it('uses the existing package from the Explorer and does not change its name',async()=>{
 const view=await mount(<ArchiveImportDialog imports={imports()} packages={[testPackage]} preferred={testPackage.id} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/> )
 expect(view.host.querySelectorAll('.archive-target input')).toHaveLength(0)
 expect(view.host.querySelectorAll('.archive-group')).toHaveLength(0)
 expect(view.host.querySelector('.archive-count')?.textContent).toContain('1 target')
 view.unmount()
})

it('requires a conflict choice and preserves the draft when returning from review',async()=>{
 const preview:ImportPreview={id:'plan',templates:[],targets:[{id:'a',package:testPackage,existing:true,sources:['One.zip','Two.7z'],files:4,bytes:16,conflicts:['People/a.duf']}],groups:[],skipped:0}
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 const start=vi.fn()
 const view=await mount(<ArchiveImportDialog imports={imports()} packages={[]} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={start}/> )
 await click([...view.host.querySelectorAll('button')].find(b=>b.textContent==='Review import')!)
 const primary=view.host.querySelector<HTMLButtonElement>('.dialog-footer .primary')!
 expect(primary.disabled).toBe(true)
 expect(view.host.querySelectorAll('.archive-conflict-options input')).toHaveLength(2)
 expect(view.host.querySelector('.archive-conflict-options input:checked')).toBeNull()
 expect([...view.host.querySelectorAll('.dialog-footer button')].some(b=>b.textContent==='Cancel')).toBe(true)
 await click(view.host.querySelector('input[type="radio"]')!)
 expect(primary.disabled).toBe(false)
 await click(primary)
 expect(start).toHaveBeenCalledWith(preview,'replace')
 await click([...view.host.querySelectorAll('button')].find(b=>b.textContent==='Back')!)
 expect(view.host.querySelectorAll('.archive-row')).toHaveLength(2)
 view.unmount()
})

it('renders each planned package once inside its group and labels unchanged members',async()=>{
 const second={...testPackage,id:'second',name:'Second',part:'02'}
 const unchanged={...testPackage,id:'context',name:'Context',part:'03'}
 const independent={...testPackage,id:'solo',name:'Independent'}
 const targets=[testPackage,second,independent].map(p=>({id:p.id,package:p,existing:p.id==='test',sources:['One.zip','Two.7z'],files:4,bytes:16,conflicts:p.id==='test'?['People/a.duf']:[]}))
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue({id:'plan',templates:[],targets,groups:[{id:'group',existing:true,targets:['test','second'],members:[testPackage,second,unchanged]}],skipped:0})
 const view=await mount(<ArchiveImportDialog imports={imports()} packages={[]} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/> )
 await click([...view.host.querySelectorAll('button')].find(b=>b.textContent==='Review import')!)
 const group=view.host.querySelector('.archive-review-group')!
 expect(group).not.toBeNull()
 expect(group.querySelectorAll('.archive-review-card')).toHaveLength(2)
 expect(view.host.querySelectorAll('.archive-review-card')).toHaveLength(3)
 expect(group.textContent).toContain('Context')
 expect(group.textContent).toContain('Unchanged')
 expect(view.host.querySelector('.archive-conflicts details')).not.toBeNull()
 expect(view.host.querySelector('.archive-source-line')).not.toBeNull()
 view.unmount()
})

it('uses the safe conflict-free policy and resets consent when checking again',async()=>{
 const preview:ImportPreview={id:'plan',templates:[],targets:[{id:'a',package:testPackage,existing:false,sources:['One.zip'],files:2,bytes:8,conflicts:[]}],groups:[],skipped:0}
 const check=vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 const start=vi.fn(),close=vi.fn()
 const view=await mount(<ArchiveImportDialog imports={imports()} packages={[]} t={translator('en')} language="en" onClose={close} onFinish={vi.fn()} onStart={start}/> )
 const button=(text:string)=>[...view.host.querySelectorAll('button')].find(b=>b.textContent===text)!
 await click(button('Review import'))
 await click(view.host.querySelector('.dialog-footer .primary')!)
 expect(start).toHaveBeenCalledWith(preview,'cancel')
 await click(button('Back'))
 check.mockResolvedValue({...preview,targets:[{...preview.targets[0],conflicts:['People/a.duf']}]})
 await click(button('Review import'))
 await click(view.host.querySelector('.archive-conflict-options input')!)
 await click(button('Back'))
 await click(button('Review import'))
 expect(view.host.querySelector<HTMLButtonElement>('.dialog-footer .primary')!.disabled).toBe(true)
 await click(button('Cancel'))
 expect(close).toHaveBeenCalledOnce()
 view.unmount()
})

it('summarizes actual results without counting skipped targets as extended',async()=>{
 const preview:ImportPreview={id:'plan',templates:[],targets:[testPackage,{...testPackage,id:'b',name:'Existing'}].map((p,i)=>({id:p.id,package:p,existing:!!i,sources:['One.zip'],files:2,bytes:8,conflicts:[]})),groups:[],skipped:1}
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 const state=imports(),finish=vi.fn()
 const props={packages:[],t:translator('en'),language:'en',onClose:vi.fn(),onStart:vi.fn(),onFinish:finish}
 const view=await mount(<ArchiveImportDialog {...props} imports={state}/> )
 await click([...view.host.querySelectorAll('button')].find(b=>b.textContent==='Review import')!)
 await view.render(<ArchiveImportDialog {...props} imports={{...state,session:undefined,result:{templates:[],packages:[testPackage],worklist:{entries:[],revision:1,canUndo:false,saveError:null,warnings:[]},results:[{targetId:'test',packageId:'test',status:'success',importedFiles:2,skippedFiles:0,error:null},{targetId:'b',packageId:'b',status:'skipped',importedFiles:0,skippedFiles:2,error:null}]}}}/> )
 const summary=view.host.querySelector('.archive-result-summary')!
 expect(summary.textContent).toContain('1 package created')
 expect(summary.textContent).not.toContain('0 packages extended')
 expect(summary.textContent).toContain('1 archive excluded')
 expect(summary.textContent).toContain('1 package skipped')
 expect(view.host.textContent).toContain('Skipped')
 await click([...view.host.querySelectorAll('button')].find(b=>b.textContent==='Back to worklist')!)
 expect(finish).toHaveBeenCalledOnce()
 view.unmount()
})

it.each<{status:FileStatus;imported:number;skipped:number;error:AppError|null;saveError:AppError|null;label:string;created:number}>([
 {status:'success' as const,imported:1,skipped:1,error:null,saveError:null,label:'1 file skipped',created:1},
 {status:'failed' as const,imported:0,skipped:0,error:{code:'archiveCancelled',params:{},details:null,causes:[]},saveError:null,label:'Cancelled',created:0},
 {status:'partial' as const,imported:0,skipped:0,error:{code:'rollbackFailed',params:{path:'C:/Recovery'},details:null,causes:[]},saveError:null,label:'Partially applied',created:0},
 {status:'success' as const,imported:2,skipped:0,error:null,saveError:{code:'ioError',params:{},details:null,causes:[]},label:'File access failed',created:1},
])('reports $label without claiming an unqualified success',async row=>{
 const preview:ImportPreview={id:'plan',templates:[],targets:[{id:'a',package:testPackage,existing:false,sources:['One.zip'],files:2,bytes:8,conflicts:[]}],groups:[],skipped:0}
 vi.spyOn(api,'previewArchiveImport').mockResolvedValue(preview)
 const state=imports(),props={packages:[],t:translator('en'),language:'en',onClose:vi.fn(),onFinish:vi.fn(),onStart:vi.fn()}
 const view=await mount(<ArchiveImportDialog {...props} imports={state}/>)
 await click([...view.host.querySelectorAll('button')].find(b=>b.textContent==='Review import')!)
 await view.render(<ArchiveImportDialog {...props} imports={{...state,session:undefined,result:{templates:[],packages:[],worklist:{entries:[],revision:1,canUndo:false,saveError:row.saveError,warnings:[]},results:[{targetId:'a',packageId:'test',status:row.status,importedFiles:row.imported,skippedFiles:row.skipped,error:row.error}]}}}/>)
 expect(view.host.querySelector('.archive-result-summary strong')!.textContent).toBe(row.status==='success'&&!row.saveError?'Import complete':'Review results')
 if(row.created)expect(view.host.querySelector('.archive-result-summary')!.textContent).toContain('1 package created')
 else expect(view.host.querySelector('.archive-result-summary')!.textContent).not.toContain('0 packages created')
 expect(view.host.querySelector('.archive-results')!.textContent).toContain(row.label)
 if(row.status==='partial')expect(view.host.textContent).toContain('C:/Recovery')
 view.unmount()
})

it('keeps keyboard focus and uses displayed neighbors after shared-target reassignment',async()=>{
 const data={...session,contents:['A.zip','B.zip','C.zip','D.zip'].map((archiveName,index)=>({...session.contents[0],id:String(index),archiveId:String(index),classification:'content' as const,suggestedAction:'package' as const,templateIssue:null,templatePath:null,archiveName,suggestedGroup:index<2?'group':null}))}
 const view=await mount(<ArchiveImportDialog imports={{...imports(),session:data}} packages={[]} t={translator('en')} language="en" onClose={vi.fn()} onFinish={vi.fn()} onStart={vi.fn()}/> )
 await click(view.host.querySelector('#archive-target-3')!)
 await click([...view.host.querySelectorAll('[role="option"]')].find(item=>item.textContent==='Into “A”')!)
 const order=()=>[...view.host.querySelectorAll('[data-archive-row]')].map(row=>row.getAttribute('data-archive-row'))
 expect(document.activeElement?.id).toBe('archive-target-3')
 expect(order()).toEqual(['0','1','3','2'])
 await act(async()=>{const row=view.host.querySelector<HTMLElement>('[data-archive-row="3"]')!;row.focus();row.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))})
 expect(order()).toEqual(['0','3','1','2'])
 expect(view.host.querySelectorAll('.archive-group')).toHaveLength(1)
 expect(document.activeElement?.getAttribute('data-archive-row')).toBe('3')
 await act(async()=>{document.activeElement?.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))})
 expect(document.activeElement?.getAttribute('data-archive-row')).toBe('3')
 view.unmount()
})
