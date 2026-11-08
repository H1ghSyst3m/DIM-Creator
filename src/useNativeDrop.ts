import {useEffect,useEffectEvent,useLayoutEffect,useRef,useState} from 'react'
import {isTauri} from '@tauri-apps/api/core'
import {api,type NativeDropEvent} from './api'

type DropPreview={area:string;valid:boolean;count:number;x:number;y:number;folder:string}
type DropState={archives:boolean;preview:DropPreview|null}

export function useNativeDrop({blocked,packageId,folder,onImport,onCopy,onCover,onError}:{
  blocked:boolean;packageId?:string;folder:string
  onImport:(paths:string[],preferred?:string)=>void
  onCopy:(paths:string[],folder:string)=>void
  onCover:(path:string)=>void
  onError:(error:unknown)=>void
}) {
  const paths=useRef<string[]>([])
  const position=useRef<{type:'over';x:number;y:number}|null>(null)
  const [state,setState]=useState<DropState>({archives:false,preview:null})
  const clear=()=>{paths.current=[];position.current=null;setState({archives:false,preview:null})}
  const receive=useEffectEvent((event:NativeDropEvent)=>{
    if(event.type==='leave'||blocked){clear();return}
    if(event.type!=='over')paths.current=event.paths
    position.current={type:'over',x:event.x,y:event.y}
    const sources=paths.current
    if(!sources.length)return
    const archives=sources.map(path=>/\.(zip|rar|7z)$/i.test(path))
    const hit=document.elementFromPoint(event.x,event.y)
    const area=hit?.closest<HTMLElement>('[data-drop-target]')?.dataset.dropTarget
    const destination=hit?.closest<HTMLElement>('[data-drop-folder]')?.dataset.dropFolder??folder
    const supported=area==='worklist'||!!packageId&&(area==='archive'||area==='files'||area==='cover')
    const valid=area==='worklist'||area==='archive'?archives.every(Boolean):area==='cover'?sources.length===1&&/\.(jpe?g|png|webp)$/i.test(sources[0]):true
    if(event.type!=='drop'){
      setState({archives:archives.some(Boolean),preview:supported?{area:area!,valid,count:sources.length,x:event.x,y:event.y,folder:destination}:null})
      return
    }
    clear()
    if(!supported)return
    if(!valid){onError({code:area==='cover'?'unsupportedCoverFormat':'archiveDropOnly',params:{},details:null});return}
    if(area==='worklist'||area==='archive')onImport(sources,area==='archive'?packageId:undefined)
    else if(area==='files')onCopy(sources,destination)
    else if(area==='cover')onCover(sources[0])
  })
  useLayoutEffect(()=>{
    // Revealing the import zone changes hit geometry even if the pointer stays still.
    if(position.current)receive(position.current)
  },[state.archives])
  useEffect(()=>{
    if(blocked)clear()
  },[blocked])
  useEffect(()=>{
    if(!isTauri())return
    let closed=false
    let unlisten:(()=>void)|undefined
    const blur=()=>clear()
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')clear()}
    window.addEventListener('blur',blur)
    window.addEventListener('keydown',escape)
    void api.onDrop(event=>{if(!closed)receive(event)}).then(listener=>{
      if(closed)listener();else unlisten=listener
    }).catch(error=>{if(!closed)onError(error)})
    return()=>{closed=true;unlisten?.();window.removeEventListener('blur',blur);window.removeEventListener('keydown',escape)}
  },[])
  return state
}
