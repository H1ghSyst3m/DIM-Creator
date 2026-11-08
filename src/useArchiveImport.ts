import {useRef, useState} from 'react'
import {api, type ArchiveJobEvent, type ArchiveSessionView, type CopyPolicy, type ImportPreview, type ImportResult} from './api'
import type {useWorkspace} from './useWorkspace'

export function useArchiveImport(workspace: ReturnType<typeof useWorkspace>, onImported: ()=>void) {
  const [session,setSession]=useState<ArchiveSessionView>()
  const [event,setEvent]=useState<ArchiveJobEvent>()
  const [result,setResult]=useState<ImportResult>()
  const [error,setError]=useState<unknown>()
  const [running,setRunning]=useState(false)
  const [cancelling,setCancelling]=useState(false)
  const active=useRef(false)
  const generation=useRef(0)
  const job=useRef<string|undefined>(undefined)
  const cancelRequested=useRef(false)
  const startJob=async (start:(receive:(value:ArchiveJobEvent)=>void)=>Promise<string>)=>{
    const current=++generation.current
    active.current=true;cancelRequested.current=false;job.current=undefined
    setRunning(true);setCancelling(false);setError(undefined);setEvent(undefined)
    try{
      const id=await start(value=>{
        if(generation.current!==current || !active.current) return
        job.current=value.jobId;setEvent(value)
        if(value.finished){
          active.current=false;setRunning(false)
          if(value.session) setSession(value.session)
          if(value.result){setResult(value.result);workspace.adoptImport(value.result);if(value.result.packages.length)onImported()}
          if(value.error) setError(value.error)
        }
      })
      job.current=id
      if(cancelRequested.current&&active.current) await api.cancelArchiveJob(id)
    }catch(error){active.current=false;setRunning(false);setError(error)}
  }
  const analyze=async(sources:string[])=>{
    setSession(undefined);setResult(undefined)
    await startJob(receive=>api.analyzeArchives(sources,receive))
  }
  const start=async(preview:ImportPreview,policy:CopyPolicy)=>{
    if(!session) return
    const id=session.id
    setSession(undefined)
    await startJob(receive=>api.startArchiveImport(id,preview.id,policy,receive))
  }
  const cancel=async()=>{
    if(!active.current||cancelRequested.current)return
    cancelRequested.current=true;setCancelling(true)
    try{if(job.current) await api.cancelArchiveJob(job.current)}
    catch(error){cancelRequested.current=false;setCancelling(false);setError(error)}
  }
  const close=async()=>{
    if(active.current)return
    if(session)await api.releaseArchiveSession(session.id)
    generation.current++;setSession(undefined);setResult(undefined);setEvent(undefined);setError(undefined)
  }
  return {session,event,result,error,running,cancelling,analyze,start,cancel,close,isRunning:()=>active.current}
}
