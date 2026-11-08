import { expect, it } from 'vitest'
import { createTranslator, errorText, languages, resolveLanguage, sizeText } from './messages'
import { de } from './locales/de'
import type { CatalogEntry } from './locales/types'
import { resolveTheme, themeGroups, themeLabel, themes } from './themes'

it('keeps all language keys, placeholders and plural fallbacks in sync', () => {
  const placeholders = (entry: CatalogEntry) => [...new Set((typeof entry === 'string' ? [entry] : Object.values(entry)).flatMap(text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1])))].sort()
  for (const { catalog } of languages) {
    expect(Object.keys(catalog).sort()).toEqual(Object.keys(de).sort())
    for (const key of Object.keys(de) as (keyof typeof de)[]) {
      expect(placeholders(catalog[key]), key).toEqual(placeholders(de[key]))
      if (typeof catalog[key] !== 'string') expect(catalog[key]).toHaveProperty('other')
    }
  }
})

it('supports additional plural categories through one registered catalog', () => {
  const language = { id: 'fixture', name: 'Test', locale: 'ar', catalog: { ...de, allPackages: { zero: 'zero', one: 'one', two: 'two', few: 'few {count}', many: 'many {count}', other: 'other {count}' } } }
  const t = createTranslator(language)
  expect([0,1,2,3,11,100].map(count => t('allPackages',{count}))).toEqual(['zero','one','two','few 3','many 11','other 100'])
  expect(resolveLanguage('fixture',[...languages,language])).toBe(language)
  expect(resolveLanguage('unknown').id).toBe('de')
  expect(sizeText(1024,'unknown')).toBe('1 KiB')
})

it('localizes stored errors at render time and preserves recovery paths', () => {
  const error = { code:'rollbackFailed',params:{path:'C:/Recovery'},details:null,causes:[{code:'reservedNativeName',params:{name:'CON .txt'},details:null,causes:[]}] }
  expect(errorText(error,createTranslator(languages[0]))).toContain('Reservierter Windows-Dateiname: CON .txt')
  expect(errorText(error,createTranslator(languages[1]))).toContain('Recovery files: C:/Recovery')
})

it('groups additional registered categories without changing the stored ID fallback', () => {
  const extra = { ...themes[0], id:'fixture', category:'theme' as const }
  const registry = [...themes,extra]
  expect(themeGroups(registry).map(group=>group.category)).toEqual(['classic','glass','theme'])
  expect(themeGroups(registry)[2].schemes[0].themes).toEqual([extra])
  expect(resolveTheme('unknown').id).toBe('dark')
  expect(resolveTheme('fixture',registry)).toBe(extra)
})

it('registers eight stable IDs with shared Classic/Glass and light/dark grouping', () => {
  const ids = ['dark','light','graphite','polar','evergreen','sand','frost','nocturne']
  expect(themes.map(theme => theme.id)).toEqual(ids)
  expect(themes.map(theme => theme.colorScheme)).toEqual(['dark','light','dark','light','dark','light','light','dark'])
  expect(themes.map(theme=>theme.category)).toEqual(['classic','classic','classic','classic','classic','classic','glass','glass'])
  const groups=themeGroups()
  expect(groups).toHaveLength(2)
  expect(groups[0].schemes.map(group=>[group.colorScheme,group.themes.map(theme=>theme.id)])).toEqual([
    ['light',['light','polar','sand']],['dark',['dark','graphite','evergreen']],
  ])
  expect(groups[1].schemes.map(group=>[group.colorScheme,group.themes.map(theme=>theme.id)])).toEqual([
    ['light',['frost']],['dark',['nocturne']],
  ])
  for (const language of languages) {
    const t=createTranslator(language)
    expect(themeLabel(resolveTheme('dark'),t)).toBe(`Aqua · ${t('dark')}`)
    expect(themeLabel(resolveTheme('light'),t)).toBe(`Aqua · ${t('light')}`)
    expect(groups[0].schemes.map(group=>group.themes.map(theme=>t(theme.label)))).toEqual([
      ['Aqua','Polar','Sand'],['Aqua','Graphite','Evergreen'],
    ])
    expect(groups[1].schemes.map(group=>group.themes.map(theme=>t(theme.label)))).toEqual([['Frost'],['Nocturne']])
  }
})
