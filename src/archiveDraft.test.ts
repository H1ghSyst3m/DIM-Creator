import { describe, expect, it } from 'vitest'
import { createImportDraft, moveArchive, retargetArchive, setArchiveAction } from './archiveDraft'
import type { ArchiveSessionView } from './api'

const session: ArchiveSessionView = {id:'session',templateDirectoryIssue:null,contents:['First.zip','Second.7z','Third.rar'].map((name,index)=>({id:String(index),archiveId:String(index),classification:'content' as const,suggestedAction:'package' as const,templateIssue:null,templatePath:null,archiveName:name,origin:[],roots:[{id:'root',path:'Content',folders:['People'],files:1,bytes:3}],suggestedGroup:index<2?'group':null,issue:null}))}
describe('archive target assignments',()=>{
  it('keeps grouping on unique targets when several sources share a package',()=>{
    const initial=createImportDraft(session,[])
    const target=initial.assignments[0].targetId
    const draft=retargetArchive(initial,'1',target)
    expect(draft.assignments[1].targetId).toBe(target)
    expect(draft.groups).toEqual([])
    expect(draft.targets.find(item=>item.id===target)?.name).toBe('First')
  })
  it('groups and detaches all rows belonging to the same target',()=>{
    const initial=createImportDraft(session,[])
    const grouped=moveArchive(initial,session,'2','0','join')
    expect(grouped.groups[0].targets).toHaveLength(3)
    const detached=moveArchive(grouped,session,'2',null,'outside')
    expect(detached.groups[0].targets).toHaveLength(2)
    expect(detached.assignments.at(-1)?.contentId).toBe('2')
  })
  it('does not guess ambiguous content roots',()=>{
    const value=structuredClone(session)
    value.contents[0].roots.push({...value.contents[0].roots[0],id:'other',path:'Content/Other'})
    expect(createImportDraft(value,[]).assignments[0].rootId).toBe('')
  })
})

it('keeps the target group when only reordering its shared archive sources',()=>{
  const initial=createImportDraft({...session,contents:session.contents.map(item=>({...item,suggestedGroup:'group'}))},[])
  const shared=retargetArchive(initial,'1',initial.assignments[0].targetId)
  expect(shared.groups[0].targets).toHaveLength(2)
  const reordered=moveArchive(shared,session,'0','1','after')
  expect(reordered.groups).toEqual(shared.groups)
  expect(reordered.assignments.map(item=>item.contentId)).toEqual(['1','0','2'])
})

it('uses Rust action suggestions and never groups template-only sources',()=>{
  const value:ArchiveSessionView={...session,contents:session.contents.map((item,index)=>({...item,
    suggestedAction:index===0?'template':'exclude',classification:'template',roots:[],
  }))}
  const draft=createImportDraft(value,[])
  expect(draft.assignments.map(item=>item.action)).toEqual(['template','exclude','exclude'])
  expect(draft.groups).toEqual([])
})

it('places a row after every hidden content unit of a template target',()=>{
  const value:ArchiveSessionView={...session,contents:session.contents.map((item,index)=>({...item,archiveId:index?'shared':item.archiveId}))}
  const draft=setArchiveAction(createImportDraft(value,[]),value,'1','template')
  const moved=moveArchive(draft,value,'0','1','after')
  expect(moved.assignments.map(item=>item.contentId)).toEqual(['1','2','0'])
  const restored=setArchiveAction(moved,value,'1','package')
  expect(restored.assignments.map(item=>item.contentId)).toEqual(['1','2','0'])
  expect(restored.groups).toEqual([])
})

it('reorders an excluded source without changing the group of its shared package target',()=>{
  const grouped=moveArchive(createImportDraft(session,[]),session,'2','0','join')
  const shared=retargetArchive(grouped,'1',grouped.assignments[0].targetId)
  const excluded=setArchiveAction(shared,session,'0','exclude')
  expect(excluded.groups).toHaveLength(1)
  const moved=moveArchive(excluded,session,'0',null,'outside')
  expect(moved.groups).toEqual(excluded.groups)
  expect(moved.assignments.at(-1)?.contentId).toBe('0')
})
