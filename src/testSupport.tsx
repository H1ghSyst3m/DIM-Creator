import { act, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import type { Package, Settings } from './api'

export const testSettings: Settings = {language:'de',theme:'dark',stores:[{name:'LOCAL USER',prefix:'LOCAL'}],tags:['General'],defaultStore:'LOCAL USER',defaultTags:[],dazFolders:['People'],checkUpdates:false,outputDir:'C:/Output',inspectorWidth:null,outputOrganization:'flat',templateDir:null,autoSaveTemplates:false}
export const testPackage: Package = {id:'test',name:'Test',store:'LOCAL USER',prefix:'LOCAL',autoPrefix:true,sku:'1',part:'01',guid:'16b2ad76-fd22-4cab-a43a-3014671584fd',tags:[],cover:null,cleanSupport:true,excluded:[]}

export async function mount(content: ReactNode) {
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  await act(async () => root.render(content))
  return { host, render: async (next: ReactNode) => { await act(async () => root.render(next)) }, unmount: () => { act(() => root.unmount()); host.remove() } }
}

export async function input(element: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', {bubbles:true}))
  })
}

export async function click(element: Element) {
  await act(async () => element.dispatchEvent(new MouseEvent('click', {bubbles:true})))
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return {promise, resolve, reject}
}
