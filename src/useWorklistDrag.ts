import { useEffect, useRef, useState, type PointerEvent, type RefObject } from 'react'
import type { WorklistAction, WorklistSource, WorklistTarget } from './api'

type Drop = { target: WorklistTarget; line?: {left: number; top: number; width: number}; highlight?: string }
type Drag = { source: WorklistSource; ids: string[]; x: number; y: number; drop: Drop | null }
type Session = Drag & {startX: number; startY: number; pointer: number; element: HTMLElement; started: boolean}

function destination(list: HTMLElement, drag: Drag): Drop | null {
  const end = list.parentElement?.querySelector('.worklist-end')?.getBoundingClientRect()
  if (end && drag.x >= end.left && drag.x <= end.right && drag.y >= end.top && drag.y <= end.bottom) return {target:{kind:'root',anchor:null,before:false}}
  const bounds = list.getBoundingClientRect()
  if (drag.x < bounds.left || drag.x > bounds.right || drag.y < bounds.top || drag.y > bounds.bottom) return null
  const blocks = [...list.querySelectorAll<HTMLElement>('[data-worklist-block]')]
  const rootLine = (block: HTMLElement | undefined, before: boolean): Drop | null => {
    if (block?.dataset.worklistBlock === (drag.source.kind === 'group' ? drag.source.id : undefined)) return null
    const rect = block?.getBoundingClientRect()
    return {target:{kind:'root',anchor:block?.dataset.worklistBlock ?? null,before},line:{left:bounds.left+10,top:rect ? before ? rect.top-4 : rect.bottom+4 : bounds.top+12,width:bounds.width-20}}
  }
  for (const block of blocks) {
    const rect = block.getBoundingClientRect()
    if (drag.y < rect.top) return rootLine(block, true)
    if (drag.y > rect.bottom) continue
    if (drag.x < rect.left || drag.x > rect.right) return rootLine(block, drag.y < rect.top+rect.height/2)
    const group = block.dataset.groupId
    if (group) {
      if (drag.y < rect.top+9) return rootLine(block, true)
      if (drag.y > rect.bottom-9) return rootLine(block, false)
      if (drag.source.kind === 'group') return drag.source.id === group ? null : {target:{kind:'join',group},highlight:group}
      const rows = [...block.querySelectorAll<HTMLElement>('[data-package-id]')]
      const allHere = drag.ids.every(id => rows.some(row => row.dataset.packageId === id))
      for (const row of rows) {
        const box = row.getBoundingClientRect()
        if (drag.y < box.top || drag.y > box.bottom || drag.x < box.left || drag.x > box.right) continue
        const edge = drag.y < box.top+box.height*.23 || drag.y > box.bottom-box.height*.23
        if (edge || allHere) {
          const anchor = row.dataset.packageId!
          const before = drag.y < box.top+box.height/2
          return drag.ids.includes(anchor) ? null : {target:{kind:'member',group,anchor,before},line:{left:box.left+5,top:before?box.top:box.bottom,width:box.width-10}}
        }
      }
      if (allHere) return null
      return {target:{kind:'join',group},highlight:group}
    }
    if (drag.source.kind === 'group' || drag.y < rect.top+rect.height*.23 || drag.y > rect.bottom-rect.height*.23) return rootLine(block, drag.y < rect.top+rect.height/2)
    const id = block.dataset.packageId!
    return drag.ids.includes(id) ? null : {target:{kind:'combine',id},highlight:id}
  }
  return rootLine(blocks.at(-1), false)
}

export function useWorklistDrag(list: RefObject<HTMLDivElement | null>, disabled: boolean, revision: number, onMove: (action: WorklistAction) => void) {
  const [drag, setDrag] = useState<Drag | null>(null)
  const session = useRef<Session | null>(null)
  const suppressClick = useRef(false)
  const onMoveRef = useRef(onMove)
  onMoveRef.current = onMove
  const cancel = () => {
    const current = session.current
    session.current = null
    if (current?.element.hasPointerCapture(current.pointer)) current.element.releasePointerCapture(current.pointer)
    setDrag(null)
    document.body.classList.remove('worklist-dragging')
  }
  useEffect(() => { cancel() }, [disabled, revision])
  useEffect(() => {
    let frame = 0
    const update = () => {
      const current = session.current
      if (!current?.started || !list.current) return
      current.drop = destination(list.current, current)
      setDrag({...current})
    }
    const scroll = () => {
      const current = session.current
      if (!current?.started || !list.current) return
      const box = list.current.getBoundingClientRect()
      if (current.x >= box.left && current.x <= box.right) {
        const delta = current.y < box.top+40 ? -Math.min(12,(box.top+40-current.y)/3) : current.y > box.bottom-40 ? Math.min(12,(current.y-box.bottom+40)/3) : 0
        if (delta) { list.current.scrollTop += delta; update() }
      }
      frame = requestAnimationFrame(scroll)
    }
    const move = (event: globalThis.PointerEvent) => {
      const current = session.current
      if (!current || event.pointerId !== current.pointer) return
      current.x = event.clientX; current.y = event.clientY
      if (!current.started && Math.hypot(current.x-current.startX,current.y-current.startY) >= 6) {
        current.started = true; suppressClick.current = true
        document.body.classList.add('worklist-dragging')
        frame = requestAnimationFrame(scroll)
      }
      if (current.started) { event.preventDefault(); update() }
    }
    const end = (event: globalThis.PointerEvent) => {
      const current = session.current
      if (!current || event.pointerId !== current.pointer) return
      const action = current.started && current.drop ? {kind:'move' as const,source:current.source,target:current.drop.target} : null
      cancelAnimationFrame(frame); cancel()
      if (action) onMoveRef.current(action)
      setTimeout(() => { suppressClick.current = false }, 0)
    }
    const abort = () => { cancelAnimationFrame(frame); cancel() }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && session.current) { event.preventDefault(); abort() } }
    window.addEventListener('pointermove',move,{passive:false})
    window.addEventListener('pointerup',end)
    window.addEventListener('pointercancel',abort)
    window.addEventListener('lostpointercapture',abort)
    window.addEventListener('blur',abort)
    window.addEventListener('keydown',escape,true)
    return () => {
      window.removeEventListener('pointermove',move); window.removeEventListener('pointerup',end); window.removeEventListener('pointercancel',abort)
      window.removeEventListener('lostpointercapture',abort)
      window.removeEventListener('blur',abort); window.removeEventListener('keydown',escape,true); cancelAnimationFrame(frame)
      const current = session.current; session.current = null
      if (current?.element.hasPointerCapture(current.pointer)) current.element.releasePointerCapture(current.pointer)
      document.body.classList.remove('worklist-dragging')
    }
  }, [list])
  const start = (event: PointerEvent<HTMLElement>, source: WorklistSource, ids: string[]) => {
    if (disabled || event.button !== 0 || !ids.length) return
    suppressClick.current = false
    session.current = {source,ids,x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,pointer:event.pointerId,element:event.currentTarget,started:false,drop:null}
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  return {drag,start,suppressClick}
}
