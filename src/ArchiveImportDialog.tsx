import {useCallback,useEffect,useLayoutEffect,useRef,useState,type PointerEvent} from 'react'
import {api,type ArchiveContent,type ArchiveSessionView,type ImportDraft,type ImportPreview,type ImportTargetPreview,type ImportResult,type TemplatePreview,type Package,type CopyPolicy} from './api'
import {Modal} from './dialogs'
import {SingleSelect} from './SingleSelect'
import {Icon} from './Icon'
import {errorText,sizeText,type Translate} from './messages'
import {createImportDraft,moveArchive,retargetArchive,setArchiveAction} from './archiveDraft'
import type {useArchiveImport} from './useArchiveImport'

type Drop={id:string|null;mode:'join'|'before'|'after'|'outside';root?:boolean}
type Drag={id:string;pointer:number;element:HTMLElement;x:number;y:number;active:boolean;drop:Drop|null}

export function ArchiveImportDialog({imports,packages,preferred,t,language,onClose,onFinish,onStart}: {
  imports:ReturnType<typeof useArchiveImport>;packages:Package[];preferred?:string;t:Translate;language:string
  onClose:()=>void;onFinish:()=>void;onStart:(preview:ImportPreview,policy:CopyPolicy)=>Promise<void>
}){
  const [review,setReview]=useState<ImportPreview>()
  const [error,setError]=useState<unknown>()
  const [checking,setChecking]=useState(false)
  const [policy,setPolicy]=useState<'replace'|'skip'|null>(null)
  const [source,setSource]=useState<ArchiveSessionView>()
  const body=useRef<HTMLDivElement>(null)
  useEffect(()=>{body.current?.focus();body.current?.scrollTo?.(0,0)},[review,imports.result])
  const current=imports.session
  const [draft,setDraft]=useState<ImportDraft>()
  const loaded=useRef<string|undefined>(undefined)
  useEffect(()=>{if(current&&loaded.current!==current.id){loaded.current=current.id;setSource(current);setDraft(createImportDraft(current,packages,preferred));setReview(undefined)}},[current,packages,preferred])
  const check=async()=>{
    if(!current||!draft)return
    setChecking(true);setError(undefined)
    try{setReview(await api.previewArchiveImport(current.id,draft));setPolicy(null)}
    catch(error){setError(error)}finally{setChecking(false)}
  }
  const start=async()=>{
    if(!review)return
    setChecking(true);setError(undefined)
    try{await onStart(review,conflicts?policy!:'cancel')}catch(error){setError(error)}finally{setChecking(false)}
  }
  const busy=imports.running||checking
  const result=imports.result
  const included=draft?.assignments.filter(item=>item.action==='package')??[]
  const templateIds=new Set(draft?.assignments.filter(item=>item.action==='template').map(item=>source?.contents.find(content=>content.id===item.contentId)!.archiveId))
  const count=new Set(included.map(item=>item.targetId)).size
  const failure=error??imports.error
  const conflicts=!!review&&(review.targets.some(target=>target.conflicts.length)||review.templates.some(target=>target.conflicts.length))
  const steps=!imports.running&&!result
  const activeArchives=new Set(draft?.assignments.filter(item=>item.action!=='exclude').map(item=>source?.contents.find(content=>content.id===item.contentId)!.archiveId))
  const excluded=source?.contents.filter((content,index,all)=>!activeArchives.has(content.archiveId)&&all.findIndex(item=>item.archiveId===content.archiveId)===index)??[]
  const reviewCounts=review?[
    review.targets.length&&t('archivePackages',{count:review.targets.length}),
    review.templates.length&&t('templateCount',{count:review.templates.length}),
  ].filter(Boolean).join(' · '):''
  return <Modal title={t('archiveTitle')} t={t} wide className="archive-dialog" onClose={busy?undefined:onClose} footer={<>
    <span className="archive-count">{result?null:review?t('archiveOriginals'):<>{count?t('archiveSelection',{included:activeArchives.size,total:new Set(current?.contents.map(item=>item.archiveId)).size,targets:count,count}):t('archiveIncluded',{count:activeArchives.size,total:new Set(current?.contents.map(item=>item.archiveId)).size})}{templateIds.size>0&&<> · {t('templateSelection',{count:templateIds.size})}</>}</>}</span>
    {imports.running?<button type="button" disabled={imports.cancelling} onClick={()=>void imports.cancel()}>{t(imports.cancelling?'archiveCancelling':'cancel')}</button>
      :result?<button type="button" className="primary" onClick={onFinish}>{t('archiveBackToWorklist')}</button>
      :!current&&imports.error?<button type="button" onClick={onClose}>{t('close')}</button>
      :<><button type="button" disabled={checking} onClick={onClose}>{t('cancel')}</button>
      {review&&<button type="button" disabled={checking} onClick={()=>{setReview(undefined);setError(undefined)}}>{t('syncBack')}</button>}
      <button type="button" className="primary" disabled={checking||!(count+templateIds.size)||included.some(item=>!item.rootId)||templateIds.size>0&&!!current?.templateDirectoryIssue||!!review&&conflicts&&policy===null} onClick={()=>void(review?start():check())}>{review?t('archiveStart'):t('archiveReview')}<Icon name={review?'check':'chevron'}/></button></>}
  </>}>
    {steps&&<div className="sync-kicker" aria-label={t('archiveSteps')}><span className={`sync-step${!review?' active':''}`} aria-current={!review?'step':undefined}><span className="number">1</span>{t('archiveAssign')}</span><span className="sync-step-line"/><span className={`sync-step${review?' active':''}`} aria-current={review?'step':undefined}><span className="number">2</span>{t('syncCheck')}</span></div>}
    {source&&!imports.running&&!result&&<div className="archive-source-line"><Icon name="archive"/><strong>{[...new Set(source.contents.map(item=>item.origin[0]??item.archiveName))].join(' · ')}</strong><span>{t('archiveDetected',{count:new Set(source.contents.map(item=>item.archiveId)).size})}</span></div>}
    <div className="archive-body" ref={body} tabIndex={-1}>
      {failure!=null&&<p className="error" role="alert">{errorText(failure,t)}</p>}
      {imports.running&&<div className="archive-progress" role="status"><progress/><strong>{t(imports.event?.progress.phase==='template'?'templateSaving':imports.event?.progress.phase==='import'?'archiveImporting':imports.event?.progress.phase==='copy'?'archiveCopying':imports.event?.progress.phase==='security'?'archiveSecurity':imports.event?.progress.phase==='extract'?'archiveExtracting':imports.event?.progress.phase==='recognize'?'archiveRecognizing':'archiveAnalyzing')}</strong><span title={imports.event?.progress.name}>{imports.event?.progress.name}</span><small>{sizeText(imports.event?.progress.bytes??0,language)}</small></div>}
      {result&&review&&<ArchiveResults result={result} review={review} t={t}/>}
      {!imports.running&&!result&&current&&draft&&(review?<>
        <div className="archive-summary"><strong>{reviewCounts}</strong><span>{[
          review.targets.some(target=>!target.existing)&&t('archiveNewCount',{count:review.targets.filter(target=>!target.existing).length}),
          review.targets.some(target=>target.existing)&&t('archiveExistingCount',{count:review.targets.filter(target=>target.existing).length}),
          review.groups.length&&t('archiveGroupCount',{count:review.groups.length}),
          review.skipped&&t('archiveExcludedCount',{count:review.skipped}),
        ].filter(Boolean).join(' · ')}</span></div>
        <ArchiveReview review={review} policy={policy} t={t}/>
        {!!review.templates.length&&<div className="archive-template-directory"><Icon name="folder"/><label htmlFor="template-review-directory">{t('templateDirectory')}</label><input id="template-review-directory" aria-label={t('templateDirectory')} readOnly value={review.templates[0].path.slice(0,Math.max(review.templates[0].path.lastIndexOf('/'),review.templates[0].path.lastIndexOf('\\'))+1)} /></div>}
        {review.templates.map(item=><TemplateCard key={item.archiveId} item={item} templates={review.templates} policy={policy} t={t} language={language}/>)}
        {conflicts&&<fieldset className="archive-conflict-options"><legend>{t('archiveConflictChoice')}</legend><p>{t('archiveConflictScope')}</p>{(['replace','skip'] as const).map(value=><label key={value}><input type="radio" name="archive-conflict-policy" checked={policy===value} onChange={()=>setPolicy(value)}/>{t(value)}</label>)}</fieldset>}
      </>:<>{templateIds.size>0&&current.templateDirectoryIssue&&<p className="error archive-directory-error" role="alert">{errorText(current.templateDirectoryIssue,t)} {t('templateSettingsHint')}</p>}<ArchiveAssignments session={current} draft={draft} setDraft={next=>{setDraft(next);setError(undefined)}} packages={packages} disabled={checking} t={t} language={language}/></>)}
      {!imports.running&&(review||result)&&excluded.length>0&&<details className="archive-excluded"><summary>{t('archiveExcludedCount',{count:excluded.length})}</summary><ul>{excluded.map(content=><li key={content.id}><strong>{content.archiveName}</strong><span>{content.issue?errorText(content.issue,t):t('archiveExcluded')}</span></li>)}</ul></details>}
    </div>
  </Modal>
}

function ArchiveReview({review,policy,t}:{review:ImportPreview;policy:'replace'|'skip'|null;t:Translate}) {
  const shownGroups=new Set<string>()
  return <>{review.targets.map(target=>{
    const group=review.groups.find(group=>group.targets.includes(target.id))
    if(!group)return <ArchiveTargetCard key={target.id} target={target} policy={policy} t={t}/>
    if(shownGroups.has(group.id))return null
    shownGroups.add(group.id)
    return <section className="archive-review-group" key={group.id} aria-label={t(group.existing?'archiveExtendGroup':'archiveNewGroup')}>
      <h3><Icon name="group"/>{t(group.existing?'archiveExtendGroup':'archiveNewGroup')}</h3>
      <div className="archive-review-members">{group.members.map(member=>{
        const planned=review.targets.find(target=>target.package.id===member.id)
        return planned?<ArchiveTargetCard key={member.id} target={planned} policy={policy} t={t}/>:<div className="archive-context" key={member.id}><span>{member.name||t('unnamed')}</span><small>{t('archiveUnchanged')}</small><b>Part {member.part}</b></div>
      })}</div>
    </section>
  })}</>
}

function ArchiveTargetCard({target,policy,t}:{target:ImportTargetPreview;policy:'replace'|'skip'|null;t:Translate}) {
  return <article className="archive-review-card">
    <header><Icon name="file"/><strong>{target.package.name||t('unnamed')}</strong><span>{t(target.existing?'archiveExisting':'archiveNew')}</span><b>Part {target.package.part}</b></header>
    <div className="archive-card-sources"><strong>{t('archiveSources')}</strong><ul>{target.sources.map((name,index)=><li key={index}>{name}</li>)}</ul><span className="muted">{t('archiveFiles',{count:target.files})}</span></div>
    {!!target.conflicts.length&&<div className={`archive-conflicts ${policy?'muted':'warning'}`}><details><summary>{t('archiveConflictCount',{count:target.conflicts.length})}</summary><ul>{target.conflicts.map(path=><li className="path" key={path}>{path}</li>)}</ul></details>{policy&&<p>{t(policy==='replace'?'archiveReplaceEffect':'archiveSkipEffect')}</p>}</div>}
  </article>
}

function ArchiveResults({result,review,t}:{result:ImportResult;review:ImportPreview;t:Translate}) {
  const completed=result.results.filter(row=>row.status==='success')
  const created=completed.filter(row=>!review.targets.find(target=>target.id===row.targetId)!.existing).length
  const problems=[...result.results,...result.templates].some(row=>!!row.error||row.status==='failed'||row.status==='partial')||!!result.worklist?.saveError
  const counts=[
    [created,'archiveCreatedCount'],[completed.length-created,'archiveExtendedCount'],
    [result.templates.filter(row=>row.status==='success').length,'templateSavedCount'],
    [result.results.filter(row=>row.status==='skipped'&&!row.error).length,'archiveSkippedCount'],
    [result.templates.filter(row=>row.status==='skipped'&&!row.error).length,'templateSkippedCount'],
    [review.skipped,'archiveExcludedCount'],
  ] as const
  return <div className="archive-results">
    <div className="archive-summary archive-result-summary" role="status"><strong>{t(problems?'archiveResultAttention':'archiveResultComplete')}</strong>{counts.some(([count])=>count>0)&&<span>{counts.filter(([count])=>count>0).map(([count,key])=>t(key,{count})).join(' · ')}</span>}</div>
    {result.results.map(row=>{
      const target=review.targets.find(target=>target.id===row.targetId)!
      const cancelled=row.error?.code==='archiveCancelled'
      const status=cancelled?'resultCancelled':row.status==='partial'?'archivePartial':row.status==='failed'?'resultFailed':row.status==='skipped'?'resultSkipped':target.existing?'archivePackageExtended':'archivePackageCreated'
      const tone=row.status==='partial'||row.status==='failed'&&!cancelled?'error':row.status==='success'?'success':cancelled?'warning':'muted'
      return <div key={row.targetId} className={`archive-result ${tone}`}><Icon name={tone==='success'?'check':tone==='error'?'error':'info'}/><div><strong>{target.package.name||t('unnamed')}</strong><span className="archive-result-state">{t(status)}</span>{row.error&&!cancelled&&<p>{errorText(row.error,t)}</p>}{!row.error&&<p>{[row.importedFiles&&t('archiveImported',{count:row.importedFiles}),row.skippedFiles&&t('archiveFilesSkipped',{count:row.skippedFiles})].filter(Boolean).join(' · ')}</p>}</div></div>
    })}
    {result.templates.map(row=>{
      const target=review.templates.find(item=>item.archiveId===row.archiveId)!
      const source=templateSource(target,review.templates,t)
      const sameName=review.templates.some(other=>other.archiveId!==target.archiveId&&other.name===target.name)
      const cancelled=row.error?.code==='archiveCancelled'
      const tone=row.status==='partial'?'error':cancelled?'warning':row.status==='success'?'success':row.status==='failed'?'error':'muted'
      return <div key={row.archiveId} className={`archive-result ${tone}`}><Icon name={tone==='success'?'check':tone==='error'?'error':'info'}/><div>
        <strong>{sameName?source.label:target.name}</strong><span className="archive-result-state">{t(cancelled?'resultCancelled':row.status==='success'?'templateSaved':row.reason==='existing'?'templateSkippedExisting':row.reason==='collision'?'templateSkippedCollision':row.status==='skipped'?'resultSkipped':row.status==='partial'?'archivePartial':'resultFailed')}</span>
        {source.ambiguous&&<input className="archive-origin-path" readOnly aria-label={t('templateArchiveSource',{name:source.label})} value={target.sourceLocation}/>}
        {row.error&&!cancelled&&<p>{errorText(row.error,t)}</p>}
      </div></div>
    })}
    {result.worklist?.saveError&&<p className="error" role="alert">{errorText(result.worklist.saveError,t)}</p>}
  </div>
}

function templateSource(item:TemplatePreview,templates:TemplatePreview[],t:Translate){
  const origin=item.origin.join(' › ')
  const ambiguous=templates.some(other=>other.archiveId!==item.archiveId&&other.name===item.name&&other.origin.join(' › ')===origin)
  return {ambiguous,label:ambiguous?t('templateArchiveNumber',{number:templates.indexOf(item)+1,name:item.name}):[...item.origin,item.name].join(' › ')}
}

function TemplateCard({item,templates,policy,t,language}:{item:TemplatePreview;templates:TemplatePreview[];policy:'replace'|'skip'|null;t:Translate;language:string}){
  const peers=templates.filter(other=>item.conflictingArchives.includes(other.archiveId))
  const winner=policy&&[item,...peers].find(other=>policy==='replace'?other.saveOnReplace:other.saveOnSkip)
  const {ambiguous}=templateSource(item,templates,t)
  const archiveLabel=(archive:TemplatePreview)=>templateSource(archive,templates,t).label
  const effect=policy==='skip'&&item.existingPath?t('templateKeepExisting')
    :winner?.archiveId===item.archiveId?t(item.existingPath?'templateReplaceExisting':'templateWillSave')
    :winner?t('templateUseOther',{name:archiveLabel(winner)}):null
  return <article className="archive-review-card archive-template-card">
    <header><Icon name="archive"/><strong>{ambiguous?archiveLabel(item):item.name}</strong><span>{t('templateSave')}</span></header>
    <div className="archive-card-sources"><strong>{t('archiveSourceLocation')}</strong>{ambiguous?<input className="archive-origin-path" readOnly aria-label={t('templateArchiveSource',{name:archiveLabel(item)})} value={item.sourceLocation}/>:<span className="archive-origin">{item.origin.length?item.origin.join(' › '):t('archiveDirectSource')}</span>}<span className="muted">{sizeText(item.bytes,language)}</span></div>
    {!!item.conflicts.length&&<div className="archive-template-conflicts">
      {item.existingPath&&<p className={policy?'muted':'warning'}>{t('templateFileExists',{name:item.existingPath.split(/[\\/]/).pop()!})}</p>}
      {peers.length>0&&<details><summary>{t('templateSameName',{count:peers.length})}</summary><ul>{peers.map(other=><li key={other.archiveId}>{archiveLabel(other)}</li>)}</ul></details>}
      {effect&&<p className="archive-conflict-effect">{effect}</p>}
    </div>}
  </article>
}

function ArchiveAssignments({session,draft,setDraft,packages,disabled,t,language}:{session:ArchiveSessionView;draft:ImportDraft;setDraft:(draft:ImportDraft)=>void;packages:Package[];disabled:boolean;t:Translate;language:string}){
  const list=useRef<HTMLDivElement>(null)
  const focusTarget=useRef<string|null>(null)
  useLayoutEffect(()=>{if(focusTarget.current){document.getElementById(`archive-target-${focusTarget.current}`)?.focus();focusTarget.current=null}},[draft])
  const focusRow=useRef<string|null>(null)
  useLayoutEffect(()=>{if(focusRow.current){list.current?.querySelector<HTMLElement>(`[data-archive-row="${focusRow.current}"]`)?.focus();focusRow.current=null}},[draft])
  const pointer=useRef<Drag|null>(null)
  const frame=useRef(0)
  const [drag,setDrag]=useState<Drag|null>(null)
  const cancel=useCallback(()=>{const value=pointer.current;pointer.current=null;cancelAnimationFrame(frame.current);setDrag(null);if(value?.element.hasPointerCapture?.(value.pointer))value.element.releasePointerCapture(value.pointer)},[])
  useEffect(()=>{
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&pointer.current){event.preventDefault();event.stopImmediatePropagation();cancel()}}
    document.addEventListener('keydown',escape,true);window.addEventListener('blur',cancel)
    return()=>{document.removeEventListener('keydown',escape,true);window.removeEventListener('blur',cancel);cancelAnimationFrame(frame.current)}
  },[cancel])
  const dropAt=(x:number,y:number):Drop|null=>{
    const host=list.current
    if(!host)return null
    const viewport=host.closest('.archive-body')!.getBoundingClientRect()
    if(y<viewport.top||y>viewport.bottom)return null
    const hit=document.elementFromPoint(x,y)
    if(hit?.closest('[data-archive-outside]'))return {id:null,mode:'outside'}
    const row=hit?.closest<HTMLElement>('[data-archive-row]')
    if(!row||!host.contains(row)||row.dataset.excluded==='true')return null
    const box=row.getBoundingClientRect(),position=(y-box.top)/box.height
    const group=row.closest('.archive-group')?.getBoundingClientRect()
    const mode=position<.23?'before':position>.77?'after':'join'
    if(mode==='join'&&[pointer.current?.id,row.dataset.archiveRow].some(id=>draft.assignments.find(item=>item.contentId===id)?.action!=='package'))return null
    return {id:row.dataset.archiveRow!,mode,root:!!group&&x<group.left+38}
  }
  const scroll=()=>{
    const value=pointer.current,container=list.current?.closest('.archive-body')
    if(!value?.active||!container)return
    const box=container.getBoundingClientRect()
    const movement=value.y<box.top+40?-12:value.y>box.bottom-40?12:0
    if(movement){container.scrollTop+=movement;value.drop=dropAt(value.x,value.y);setDrag({...value})}
    frame.current=requestAnimationFrame(scroll)
  }
  const down=(event:PointerEvent<HTMLElement>,id:string)=>{
    if(disabled||event.button!==0||!event.currentTarget.contains(event.target as Node)||(event.target as HTMLElement).closest('button,input,label,[role="combobox"]'))return
    pointer.current={id,pointer:event.pointerId,element:event.currentTarget,x:event.clientX,y:event.clientY,active:false,drop:null}
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const move=(event:PointerEvent)=>{
    const value=pointer.current
    if(!value||value.pointer!==event.pointerId)return
    if(!value.active&&Math.hypot(event.clientX-value.x,event.clientY-value.y)<6)return
    if(!value.active){value.active=true;frame.current=requestAnimationFrame(scroll)}
    value.x=event.clientX;value.y=event.clientY;value.drop=dropAt(value.x,value.y);setDrag({...value})
  }
  const up=()=>{const value=pointer.current;if(value?.active&&value.drop)setDraft(moveArchive(draft,session,value.id,value.drop.id,value.drop.mode,value.drop.root));cancel()}
  const row=(content:ArchiveContent)=>{
    const assignment=draft.assignments.find(item=>item.contentId===content.id)!
    const target=draft.targets.find(item=>item.id===assignment.targetId)!
    const root=content.roots.find(root=>root.id===assignment.rootId)
    const ownNew=!target.packageId&&draft.assignments.find(item=>item.targetId===target.id)?.contentId===content.id
    const shared=draft.assignments.filter(item=>item.targetId===target.id&&item.action==='package')
    const nameOwner=shared[0]?.contentId===content.id
    const targetOptions=[
      ...(content.roots.length?[{value:'new',label:t('archiveNew')},...draft.targets.filter(item=>!item.packageId&&!(ownNew&&item.id===target.id)&&draft.assignments.some(assignment=>assignment.action==='package'&&assignment.targetId===item.id)).map(item=>({value:item.id,label:t('archiveTo',{name:item.name})})),...draft.targets.filter(item=>!!item.packageId).map(item=>({value:item.id,label:`${item.name} · Part ${packages.find(p=>p.id===item.packageId)?.part??''}`}))]:[]),
      ...(!content.templateIssue?[{value:'template',label:t('templateSave')}]:[]),{value:'exclude',label:t('archiveExclude')},
    ]
    const update=(values:Partial<typeof assignment>)=>setDraft({...draft,assignments:draft.assignments.map(item=>item.contentId===content.id?{...item,...values}:item)})
    return <div key={content.id} className={`archive-row${assignment.action==='exclude'?' excluded':''}${drag?.id===content.id?' dragging':''}${drag?.drop?.id===content.id?' drop-'+drag.drop.mode+(drag.drop.root?' root-drop':''):''}`} data-archive-row={content.id} data-excluded={!!content.issue} tabIndex={content.issue?-1:0} aria-label={t('archiveRow',{name:content.archiveName})}
      onPointerDown={event=>!content.issue&&down(event,content.id)} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel} onLostPointerCapture={cancel}
      onKeyDown={event=>{if(event.target!==event.currentTarget||disabled)return;const order=blocks.flatMap(block=>block.rows.map(row=>row.id));const index=order.indexOf(content.id);if(event.key==='ArrowUp'&&index>0){event.preventDefault();focusRow.current=content.id;setDraft(moveArchive(draft,session,content.id,order[index-1],'before'))}if(event.key==='ArrowDown'&&index<order.length-1){event.preventDefault();focusRow.current=content.id;setDraft(moveArchive(draft,session,content.id,order[index+1],'after'))}}}>
      <div className="archive-source"><input type="checkbox" disabled={disabled||!!content.issue||!content.roots.length&&!!content.templateIssue} checked={assignment.action!=='exclude'} aria-label={t('archiveInclude',{name:content.archiveName})} onChange={event=>setDraft(setArchiveAction(draft,session,content.id,event.target.checked?(content.roots.length?'package':'template'):'exclude'))}/><Icon name="file"/><div><strong title={content.archiveName}>{content.archiveName}</strong><p>{content.origin.length>0&&<span title={content.origin.join(' › ')}>{t('archiveOrigin',{name:content.origin.join(' › ')})}{assignment.action==='package'&&root?' · ':''}</span>}{assignment.action==='package'&&root&&<>{t('archiveFiles',{count:root.files})} · {sizeText(root.bytes,language)}</>}</p><small className={content.issue?'warning':''}>{content.issue?errorText(content.issue,t):content.classification==='template'?t('archiveTemplate'):content.classification==='noContent'?t('archiveNoContent'):root?t('archiveContent',{folders:root.folders.join(' · ')}):t('archiveChooseRoot')}</small></div></div>
      {content.issue?<div className="muted">{t('archiveExcluded')}</div>:<div className="archive-target">
        <SingleSelect id={`archive-target-${content.id}`} label={t('archiveTarget',{name:content.archiveName})} value={assignment.action==='package'?(ownNew?'new':target.id):assignment.action} options={targetOptions} disabled={disabled} onChange={value=>{
          const first=draft.assignments.find(item=>session.contents.find(contentRow=>contentRow.id===item.contentId)!.archiveId===content.archiveId)!
          focusTarget.current=value==='template'?first.contentId:content.id
          if(value==='template'||value==='exclude'){setDraft(setArchiveAction(draft,session,content.id,value));return}
          const restored=setArchiveAction(draft,session,content.id,'package')
          if(value==='new'){
            if(ownNew){setDraft(restored);return}
            const next={id:crypto.randomUUID(),packageId:null,name:content.archiveName.replace(/\.(zip|rar|7z)$/i,'')}
            setDraft(retargetArchive({...restored,targets:[...restored.targets,next]},content.id,next.id))
          }else setDraft(retargetArchive(restored,content.id,value))
        }}/>
        {assignment.action==='package'?<>
          {target.packageId?<span className="muted">{t('archiveExistingName',{name:target.name})}</span>:nameOwner?<label className="archive-name-label">{shared.length>1&&<span>{t('archiveSharedName',{count:shared.length})}</span>}<input aria-label={t('archiveProductName',{name:content.archiveName})} value={target.name} disabled={disabled} onChange={event=>setDraft({...draft,targets:draft.targets.map(item=>item.id===target.id?{...item,name:event.target.value}:item)})}/></label>:<span className="archive-shared-target">{t('archiveSharedTarget',{name:target.name||t('unnamed')})}</span>}
          {content.roots.length>1&&<SingleSelect id={`archive-root-${content.id}`} label={t('archiveChooseRoot')} emptyText={t('archiveChooseRoot')} value={assignment.rootId} options={content.roots.map(root=>({value:root.id,label:root.path||'Content'}))} disabled={disabled} onChange={rootId=>update({rootId})}/>}</>:null}
        {content.templateIssue&&<span className="muted">{errorText(content.templateIssue,t)}</span>}
      </div>}
    </div>
  }

  const blocks:{id:string;group?:string;rows:ArchiveContent[]}[]=[]
  for(const assignment of draft.assignments){
    const content=session.contents.find(item=>item.id===assignment.contentId)!
    if(assignment.action==='template'&&draft.assignments.find(item=>item.action==='template'&&session.contents.find(row=>row.id===item.contentId)!.archiveId===content.archiveId)?.contentId!==content.id)continue
    const group=assignment.action==='package'&&!content.issue?draft.groups.find(group=>group.targets.includes(assignment.targetId)):undefined
    const block=group&&blocks.find(block=>block.group===group.id)
    if(block)block.rows.push(content);else blocks.push({id:content.id,group:group?.id,rows:[content]})
  }
  return <>
    <div className="archive-list-heading"><h3>{t('archiveFound')}</h3><span>{t('archiveIncluded',{count:new Set(draft.assignments.filter(item=>item.action!=='exclude').map(item=>session.contents.find(content=>content.id===item.contentId)!.archiveId)).size,total:new Set(session.contents.map(item=>item.archiveId)).size})}</span><button className="text" type="button" disabled={disabled} onClick={()=>{
      let next=draft
      for(const content of session.contents){
        const assignment=next.assignments.find(item=>item.contentId===content.id)!
        if(!content.issue&&assignment.action==='exclude'&&(content.roots.length||!content.templateIssue))next=setArchiveAction(next,session,content.id,content.roots.length?'package':'template')
      }
      setDraft(next)
    }}>{t('archiveIncludeAll')}</button><button className="text" type="button" disabled={disabled} onClick={()=>setDraft({...draft,groups:[],assignments:draft.assignments.map(item=>({...item,action:'exclude'}))})}>{t('syncSelectNone')}</button></div>
    <div className="archive-columns"><span>{t('archiveFile')}</span><span>{t('archiveTargetHeading')}</span></div>
    <div className="archive-list" ref={list}>{blocks.map(block=>block.group?<div className="archive-group" key={block.group}><div className="archive-gutter"><button type="button" className="icon-btn archive-ungroup" disabled={disabled} aria-label={t('dissolveGroup')} title={t('dissolveGroup')} onClick={()=>setDraft({...draft,groups:draft.groups.filter(group=>group.id!==block.group)})}><Icon name="ungroup"/></button></div><div>{block.rows.map(row)}</div></div>:block.rows.map(row))}<div className={`archive-outside${drag?.drop?.mode==='outside'?' active':''}`} data-archive-outside="true">{t('archiveOutside')}</div></div>
    <p className="archive-hint"><Icon name="info"/>{t('archiveDragHint')}</p>
    {drag&&<div className="file-drag-label" style={{left:drag.x+12,top:drag.y+12}} role="status">{t(drag.drop?.mode==='join'?'groupPackages':drag.drop?'archiveReorder':'invalidDrop')}</div>}
  </>
}
