import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import type { Package, Settings } from './api'
import { translator } from './messages'
import { displaySku, matchesStore } from './workflow'
import { Inspector } from './panels'
import { Worklist } from './Worklist'
import { SingleSelect } from './SingleSelect'
import { ExportReview } from './dialogs'
import { outputOrganizationOptions } from './outputOrganizations'
import { testPackage } from './testSupport'

it('matches store names and prefixes without changing catalog order or indices', () => {
  const stores = [{name:'DAZ 3D',prefix:'IM'}, {name:'Local User',prefix:'LOCAL'}, {name:'Renderosity',prefix:'RO'}]
  expect(stores.map((store,index) => ({store,index})).filter(({store}) => matchesStore(store,'  loCAl ')).map(row => row.index)).toEqual([1])
  expect(stores.filter(store => matchesStore(store,'im'))).toEqual([stores[0]])
  expect(stores.filter(store => matchesStore(store,'unknown'))).toEqual([])
  expect(stores.filter(store => matchesStore(store,' '))).toEqual(stores)
})

it('localizes missing SKU summaries without repeating the SKU label', () => {
  expect(displaySku('',translator('de'))).toBe('SKU fehlt')
  expect(displaySku(' ',translator('en'))).toBe('SKU missing')
  expect(displaySku('00123',translator('de'))).toBe('SKU 00123')
})

it('renders a labeled single-select with its selected value and disabled state', () => {
  const markup = renderToStaticMarkup(<SingleSelect id="language" label="Language" value="de"
    options={[{value:'de',label:'Deutsch'},{value:'en',label:'English'}]} disabled onChange={() => undefined} />)
  expect(markup).toContain('Deutsch')
  expect(markup).toContain('aria-expanded="false"')
  expect(markup).toContain('disabled=""')
  expect(markup).not.toContain('<select')
})

it('keeps properties editable without the redundant output section or native store select', () => {
  const item: Package = {id:'test',name:'Garden',store:'LOCAL USER',prefix:'LOCAL',autoPrefix:true,sku:'1',part:'01',guid:'guid',tags:[],cover:null,cleanSupport:true,excluded:[]}
  const settings: Settings = {language:'de',theme:'dark',checkUpdates:false,stores:[{name:'LOCAL USER',prefix:'LOCAL'}],tags:[],defaultStore:'LOCAL USER',defaultTags:[],dazFolders:[],inspectorWidth:null,outputDir:null,outputOrganization:'flat',templateDir:null,autoSaveTemplates:false}
  const noop = () => undefined
  const markup = renderToStaticMarkup(<Inspector item={item} settings={settings} preview={{filename:'LOCAL00000001-01_Garden.zip',entries:[],issues:[],size:0}}
    disabled={false} t={translator('de')} cover={null} shown={new Set()} reveal={noop} change={noop} chooseCover={noop} removeCover={noop}
    generateGuid={noop} manageTags={noop} manageStores={noop} build={noop} remove={noop} />)
  expect(markup).toContain('id="field-sku"')
  expect(markup).not.toContain('Ausgabedatei')
  expect(markup).not.toContain('field-output')
  expect(markup).not.toContain('<select')
})

it('labels visible selection by trimmed search without changing checked or hidden selections', () => {
  const noop = () => undefined
  for (const [language, all, search] of [['de','Alle auswählen','Suchergebnisse auswählen'],['en','Select all','Select search results']]) {
    for (const query of ['', '   ', 'test', 'no matches']) {
      const markup = renderToStaticMarkup(<Worklist packages={[testPackage]} visible={query === 'no matches' ? [] : [testPackage]}
        worklist={{revision:0,entries:[{kind:'package',id:'test'}],canUndo:false,saveError:null,warnings:[]}} onStructure={async () => false}
        active="test" previews={{}} selected={new Set(['test','hidden'])} query={query} t={translator(language)} language={language} disabled={false}
        onQuery={noop} onActive={noop} onCheck={noop} onCheckAll={noop} onClear={noop} onAdd={noop} onValidate={noop} onSelection={noop} onBuild={noop} onRemove={noop} />)
      expect(markup).toContain(query.trim() ? search : all)
      expect(markup).toContain(translator(language)('selected',{count:2}))
      if (query !== 'no matches') expect(markup).toContain('data-checked="true"')
    }
  }
})

it('registers both localized output modes and displays shared or per-package export directories', () => {
  expect(outputOrganizationOptions(translator('de'))).toEqual([{value:'flat',label:'Flach'},{value:'byDate',label:'Nach Datum'}])
  expect(outputOrganizationOptions(translator('en'))).toEqual([{value:'flat',label:'Flat'},{value:'byDate',label:'By date'}])
  const row = {id:'a',name:'A',filename:'test.zip',outputDir:'C:/A',outputPath:'C:/A/test.zip',existing:false,stamp:null,issues:[]}
  const render = (other: string) => renderToStaticMarkup(<ExportReview rows={[row,{...row,id:'b',name:'B',outputDir:other,outputPath:`${other}/test.zip`}]}
    t={translator('en')} onClose={() => {}} onStart={() => {}} />)
  expect(render('C:/A').match(/C:\/A/g)).toHaveLength(1)
  expect(render('C:/B')).toContain('C:/A')
  expect(render('C:/B')).toContain('C:/B')
})
