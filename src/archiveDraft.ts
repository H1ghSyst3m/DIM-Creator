import type { ArchiveSessionView, ImportAction, ImportDraft, Package } from './api'

export function createImportDraft(session: ArchiveSessionView, packages: Package[], preferred?: string): ImportDraft {
  const existing = packages.map(item=>({id:crypto.randomUUID(),packageId:item.id,name:item.name}))
  const targets = session.contents.map(item=>({id:crypto.randomUUID(),packageId:null,name:item.archiveName.replace(/\.(zip|rar|7z)$/i,'')}))
  const preferredTarget=existing.find(item=>item.packageId===preferred)
  const assignments=session.contents.map((item,index)=>({contentId:item.id,rootId:item.roots.length===1?item.roots[0].id:'',targetId:preferredTarget?.id??targets[index].id,action:item.suggestedAction}))
  const groups=new Map<string,string[]>()
  session.contents.forEach((item,index)=>{
    if (!item.suggestedGroup || assignments[index].action!=='package') return
    const members=groups.get(item.suggestedGroup)??[]
    if(!members.includes(assignments[index].targetId)) members.push(assignments[index].targetId)
    groups.set(item.suggestedGroup,members)
  })
  return {assignments,targets:[...targets,...existing],groups:[...groups].filter(([,targets])=>targets.length>1).map(([id,targets])=>({id,targets}))}
}

function normalize(draft: ImportDraft): ImportDraft {
  const used=new Set(draft.assignments.filter(item=>item.action==='package').map(item=>item.targetId))
  const order=[...new Set(draft.assignments.filter(item=>item.action==='package').map(item=>item.targetId))]
  return {...draft,groups:draft.groups.map(group=>({...group,targets:[...new Set(group.targets.filter(id=>used.has(id)))].sort((a,b)=>order.indexOf(a)-order.indexOf(b))})).filter(group=>group.targets.length>1)}
}

export function setArchiveAction(draft:ImportDraft,session:ArchiveSessionView,id:string,action:ImportAction):ImportDraft {
  const content=session.contents.find(item=>item.id===id)!
  const related=new Set(session.contents.filter(item=>item.archiveId===content.archiveId).map(item=>item.id))
  const wholeArchive=action==='template'||draft.assignments.some(item=>related.has(item.contentId)&&item.action==='template')
  return normalize({...draft,assignments:draft.assignments.map(item=>
    item.contentId===id||wholeArchive&&related.has(item.contentId)?{...item,action}:item)})
}

export function retargetArchive(draft: ImportDraft, content: string, targetId: string): ImportDraft {
  const previous=draft.assignments.find(item=>item.contentId===content)!
  const assignments=draft.assignments.map(item=>item.contentId===content?{...item,targetId,action:'package' as const}:item)
  const groupWithTarget=draft.groups.find(group=>group.targets.includes(targetId))
  const groups=draft.groups.map(group=>group.targets.includes(previous.targetId)&&!groupWithTarget?{...group,targets:[...group.targets,targetId]}:group)
  return normalize({...draft,assignments,groups})
}

export function moveArchive(draft: ImportDraft, session:ArchiveSessionView, source: string, target: string|null, mode: 'join'|'before'|'after'|'outside', root = false): ImportDraft {
  const from=draft.assignments.find(item=>item.contentId===source)
  const to=draft.assignments.find(item=>item.contentId===target)
  if(!from || source===target || mode==='join'&&!to) return draft
  if(mode==='join'&&(from.action!=='package'||to?.action!=='package'))return draft
  const targetArchive=session.contents.find(item=>item.id===target)?.archiveId
  const targetUnits=new Set(session.contents.filter(item=>to?.action==='template'?item.archiveId===targetArchive:item.id===target).map(item=>item.id))
  const insertionIndex=(assignments:ImportDraft['assignments'])=>!to?assignments.length:mode==='before'
    ?assignments.findIndex(item=>targetUnits.has(item.contentId))
    :assignments.map(item=>targetUnits.has(item.contentId)).lastIndexOf(true)+1
  if(from.action==='exclude'){
    const assignments=draft.assignments.filter(item=>item.contentId!==source)
    assignments.splice(insertionIndex(assignments),0,from)
    return {...draft,assignments}
  }
  if(from.action==='template'){
    const archiveId=session.contents.find(item=>item.id===source)!.archiveId
    const members=new Set(session.contents.filter(item=>item.archiveId===archiveId).map(item=>item.id))
    if(target&&members.has(target))return draft
    const moving=draft.assignments.filter(item=>members.has(item.contentId))
    const assignments=draft.assignments.filter(item=>!members.has(item.contentId))
    const index=insertionIndex(assignments)
    assignments.splice(index,0,...moving)
    return normalize({...draft,assignments})
  }
  if(mode==='join' && to?.targetId===from.targetId) return draft
  let groups=draft.groups.map(group=>({...group,targets:group.targets.filter(id=>id!==from.targetId)}))
  if(mode==='join'&&to){
    const group=groups.find(group=>group.targets.includes(to.targetId))
    if(group) group.targets.push(from.targetId)
    else groups.push({id:crypto.randomUUID(),targets:[to.targetId,from.targetId]})
  }else if(to?.action==='package'&&mode!=='outside'&&!root){
    groups=groups.map(group=>group.targets.includes(to.targetId)?{...group,targets:[...group.targets,from.targetId]}:group)
  }
  const assignments=draft.assignments.filter(item=>item.contentId!==source)
  const index=insertionIndex(assignments)
  assignments.splice(index,0,from)
  if(to?.targetId===from.targetId && mode!=='outside' && !root) return {...draft,assignments}
  return normalize({...draft,assignments,groups})
}
