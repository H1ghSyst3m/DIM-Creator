// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { App } from './App'
import { api, type WorklistState } from './api'
import { appError, translator } from './messages'
import { click, mount, testPackage, testSettings } from './testSupport'

let dispose: (() => void) | undefined
const packages = [testPackage,{...testPackage,id:'second',name:'Second'}]
const initial: WorklistState = {revision:1,canUndo:false,saveError:null,warnings:[],entries:packages.map(p=>({kind:'package',id:p.id}))}
beforeEach(()=>{
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
  vi.stubGlobal('ResizeObserver',class {observe(){} disconnect(){}})
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')}
  HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')}
  HTMLElement.prototype.scrollIntoView=vi.fn()
  vi.spyOn(api,'loadWorkspace').mockResolvedValue({packages,settings:testSettings,warnings:[],worklist:initial})
  vi.spyOn(api,'previewOutputs').mockResolvedValue([])
  vi.spyOn(api,'preview').mockResolvedValue({filename:'test.zip',entries:[],size:0,issues:[]})
  vi.spyOn(api,'listFiles').mockResolvedValue([])
})
afterEach(()=>{dispose?.();dispose=undefined;vi.restoreAllMocks();vi.unstubAllGlobals()})

it('retries only failed trash targets even while the separate structure save still fails',async()=>{
  const saveError=appError('ioError')
  const remove=vi.spyOn(api,'removePackage').mockResolvedValueOnce({...initial,revision:2,entries:[{kind:'package',id:'second'}],saveError})
    .mockRejectedValueOnce(appError('fileUnavailable')).mockResolvedValueOnce({...initial,revision:3,entries:[],saveError})
  const retry=vi.spyOn(api,'retryWorklist').mockRejectedValue(saveError)
  const ui=await mount(<App/>);dispose=ui.unmount
  await click(ui.host.querySelector('.select-all input')!)
  await act(async()=>{ui.host.querySelector('.package-list')!.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'Delete'}))})
  await click(document.querySelector('dialog .dialog-footer .danger')!)
  expect(remove.mock.calls.map(args=>args[0])).toEqual(['test','second'])
  expect(ui.host.querySelectorAll('.package-row')).toHaveLength(1)
  await click(document.querySelector('dialog .dialog-footer .danger')!)
  expect(remove.mock.calls.map(args=>args[0])).toEqual(['test','second','second'])
  expect(retry).not.toHaveBeenCalled();expect(ui.host.querySelectorAll('.package-row')).toHaveLength(0)
  expect(ui.host.querySelector('.status-bar')?.textContent).toContain(translator('de')('saveFailed'))
})

it('preserves the initially opened package when the list order changes without opening a row',async()=>{
  vi.spyOn(api,'changeWorklist').mockResolvedValue({...initial,revision:2,canUndo:true,entries:[{kind:'package',id:'second'},{kind:'package',id:'test'}]})
  const ui=await mount(<App/>);dispose=ui.unmount
  expect((ui.host.querySelector('#field-name') as HTMLInputElement).value).toBe('Test')
  const second=ui.host.querySelector('[data-package-id="second"] .package-link')!
  await act(async()=>{second.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'ArrowUp',altKey:true}))})
  expect(ui.host.querySelector('.package-row')?.getAttribute('data-package-id')).toBe('second')
  expect((ui.host.querySelector('#field-name') as HTMLInputElement).value).toBe('Test')
})
