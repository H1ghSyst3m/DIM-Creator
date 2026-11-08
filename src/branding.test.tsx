import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Logo } from './Logo'
import { resolveTheme, themes } from './themes'

const read = (path: string) => readFileSync(resolve(path))

it('renders the same decorative signet with a high-density source', () => {
  const markup = renderToStaticMarkup(<Logo />)
  expect(markup).toMatch(/<img[^>]+class="brand-logo"/)
  expect(markup).toContain('srcSet=')
  expect(markup).toContain('2x')
  expect(markup).toContain('alt=""')
  expect(markup).toContain('aria-hidden="true"')
})

it('verifies all retained branding assets and uses their original runtime copies', () => {
  expect(existsSync('branding/manifest.json')).toBe(true)
  const manifest = JSON.parse(read('branding/manifest.json').toString()) as { files: {path: string; sha256: string}[] }
  const assets = readdirSync('branding', { recursive: true, encoding: 'utf8' })
    .map(path => path.replaceAll('\\', '/'))
    .filter(path => path !== 'manifest.json' && statSync(resolve('branding', path)).isFile())
  expect(manifest.files.map(file => file.path).sort()).toEqual(assets.sort())
  for (const file of manifest.files) {
    expect(createHash('sha256').update(read(`branding/${file.path}`)).digest('hex'), file.path).toBe(file.sha256)
  }
  for (const [runtime, original] of [
    ['src-tauri/icons/icon.ico', 'tauri/src-tauri/icons/icon.ico'],
    ['src/assets/branding/logo.png', 'windows/icon-64.png'],
    ['src/assets/branding/logo@2x.png', 'windows/icon-128.png'],
    ['public/favicon.ico', 'web/favicon.ico'],
  ]) expect(read(runtime).equals(read(`branding/${original}`)), runtime).toBe(true)
  const config = JSON.parse(read('src-tauri/tauri.conf.json').toString())
  expect(config.bundle.icon).toEqual(['icons/icon.ico'])
  expect(config.bundle.windows.nsis).toMatchObject({installerIcon:'icons/icon.ico',uninstallerIcon:'icons/icon.ico'})
  expect(config.bundle.targets).toEqual(['nsis'])
})

function blend(a: string, b: string, opacity: number) {
  const expand = (hex: string) => hex.length === 4 ? '#' + [...hex.slice(1)].map(c => c + c).join('') : hex
  return '#' + [1,3,5].map(i => Math.round(parseInt(expand(a).slice(i,i+2),16) * opacity + parseInt(expand(b).slice(i,i+2),16) * (1-opacity)).toString(16).padStart(2,'0')).join('')
}

function contrast(a: string, b: string, opacity = 1) {
  if (opacity !== 1) a = blend(a,b,opacity)
  const luminance = (hex: string) => {
    const full = hex.length === 4 ? '#' + [...hex.slice(1)].map(c => c + c).join('') : hex
    return [1,3,5].map(i => parseInt(full.slice(i,i+2),16) / 255)
      .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      .reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i],0)
  }
  const [low, high] = [luminance(a),luminance(b)].sort((x,y) => x-y)
  return (high + .05) / (low + .05)
}

it('preserves the six approved palettes when moving them and exposing swatches', () => {
  const fingerprints: Record<string,string> = {
    dark:'f1c596366f076c4d003d56cddea63b7974eea8f1b6a09d6cbc6d4631cfafb5e4',
    light:'8ea36a07de90b941f839477e73acd94f3cffec308d73b9ef15710b27ee8807df',
    graphite:'8f262fc5ce67618cfe099cb17d743125afd1d486997dbbc1b8fe03f575c4aafd',
    polar:'78c569903346d6408737be78c4b03b0e376c397510a95da0c0253f81e5d6c82b',
    evergreen:'afc98d30e2ad04790c3d623e407faa402c341dd2fd318ddf6ce817aee5802127',
    sand:'694e6f05c1b35dc4fa28680d7a23d9e5a8fd15523b775aab99f4f8e9c9f6d493',
  }
  const files = readdirSync('src/themes',{recursive:true,encoding:'utf8'}).filter(path=>path.endsWith('.css')).map(path=>path.replaceAll('\\','/'))
  expect(files.sort()).toEqual([...themes.map(theme=>theme.palette),'glass/material.css'].sort())
  for (const theme of themes) {
    const css=read(`src/themes/${theme.palette}`).toString()
    expect(theme.palette).toBe(`${theme.category}/${theme.colorScheme}/${theme.label}.css`)
    expect(css).toContain(`.theme-swatch[data-theme="${theme.id}"]`)
    if (theme.category === 'classic') expect(createHash('sha256').update(css.slice(css.indexOf('{')).replace(/\s/g,'')).digest('hex'),theme.id).toBe(fingerprints[theme.id])
  }
  expect(themes.filter(theme=>theme.category==='classic').map(theme=>theme.id).sort()).toEqual(Object.keys(fingerprints).sort())
})

it('scopes glass materials, shares swatch tokens, and preserves opaque and system-color fallbacks', () => {
  const css=read('src/themes/glass/material.css').toString()
  // Every ordinary selector must opt into Glass; at-rules only gate those selectors.
  for (const block of css.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{/g)) {
    const selector=block[1].trim()
    if (!selector.startsWith('@')) for (const part of selector.split(',')) expect(part).toContain('[data-theme-category="glass"]')
  }
  // Filtering a panel/dialog itself changes the containing block of fixed-position children.
  for (const block of css.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (/backdrop-filter:\s*blur\(/.test(block[2])) {
      for (const selector of block[1].split(',')) expect(selector.trim()).toMatch(/::before$|\.popup$/)
    }
  }
  expect(css).toContain('@supports')
  expect(css).toContain('(forced-colors: none)')
  expect(css).toContain('(forced-colors: active)')
  expect(css.split('@media (forced-colors: active)')[1]).toContain('backdrop-filter: none')
  expect(css).toContain('Canvas')
  expect(css).not.toContain('forced-color-adjust: none')
  expect(css).not.toMatch(/#[\da-f]{3,8}\b/i)
  expect(css).not.toMatch(/animation:|will-change:|(?:^|[;\s])filter:\s*blur\(/)
  expect(css).toContain('.theme-swatch[data-theme-category="glass"]')
  expect(css).toContain('var(--glass-canvas)')
  expect(css).toContain('var(--glass-panel)')
  expect(css).not.toContain('linear-gradient(')
  expect(css).not.toContain('--glass-sheen')
  expect(css).not.toContain('--glass-reflection')
  expect(css).toContain('var(--glass-popup-blur)')
  expect(css).toContain('--glass-panel-blur: 16px')
  expect(css).toContain('--glass-floating-blur: 6px')
  expect(css).toContain('--glass-popup-blur: 4px')
  expect(css).toMatch(/:root\[data-theme-category="glass"\] input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\)[^{]+\{\s*border-color: var\(--accent-line\);/)
})

for (const id of ['frost','nocturne']) it(`${id} keeps text and indicators readable over composited glass layers`, () => {
  const theme=themes.find(theme=>theme.id===id)!
  expect(theme).toBeDefined()
  const css=read(`src/themes/${theme.palette}`).toString()
  const tokens=Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[\da-f]+);/gi)].map(match=>[match[1],match[2]]))
  const over=(foreground:string,background:string)=>foreground.length===9 ? blend(foreground.slice(0,7),background,parseInt(foreground.slice(7),16)/255) : foreground
  const stops=['background','glass-light-a','glass-light-b'].map(key=>tokens[key])
  // Main glass exposes the canvas; floating layers add tint without becoming opaque.
  for (const [token, maximum] of [['glass-panel', .5], ['glass-chrome', .6], ['glass-floating', .65], ['glass-popup', .7]] as const) {
    expect(tokens[token], token).toMatch(/^#[\da-f]{8}$/i)
    expect(parseInt(tokens[token].slice(7),16) / 255, token).toBeLessThanOrEqual(maximum)
  }
  expect(contrast(tokens['accent-line'],tokens.input),'empty input boundary against input fill').toBeGreaterThanOrEqual(3)
  // Component-wise bounds conservatively contain every mixture of the gradient stops.
  const extrema=[Math.min,Math.max].map(bound=>'#'+[1,3,5].map(i=>bound(...stops.map(hex=>parseInt(hex.slice(i,i+2),16))).toString(16).padStart(2,'0')).join(''))
  for(const background of [...stops,...extrema]) {
    const panel=over(tokens['glass-panel'],background)
    const dialog=over(tokens['glass-floating'],over(tokens.backdrop,panel))
    const chrome=over(tokens['glass-chrome'],background)
    const popup=over(tokens['glass-popup'],dialog)
    const panelPopup=over(tokens['glass-popup'],panel)
    // Floating layers also cover controls; large inset sections have a light input tint.
    const surfaces=[panel,chrome,dialog,popup,panelPopup,
      // Covers can contain arbitrary colors; black/white bound their luminance.
      ...[...['input','raised','hover','accent','accent-surface','group-surface'].map(key=>tokens[key]),'#000000','#ffffff'].flatMap(value=>[
        over(tokens['glass-floating'],over(tokens.backdrop,value)),
        over(tokens['glass-popup'],value),
      ])]
    for(const surface of [...surfaces,...surfaces.map(value=>blend(tokens.input,value,.5))]) {
      for(const text of ['text','muted','faint','accent','success','warning','error','blue']) expect(contrast(tokens[text],surface),`${text} on ${surface}`).toBeGreaterThanOrEqual(4.5)
      for(const indicator of ['accent','accent-line','folder-icon']) expect(contrast(tokens[indicator],surface),`${indicator} on ${surface}`).toBeGreaterThanOrEqual(3)
      const marked=blend(tokens['accent-surface'],surface,.6)
      for(const text of ['text','muted','faint']) expect(contrast(tokens[text],marked),`${text} on marked glass`).toBeGreaterThanOrEqual(4.5)
    }
  }
})

for (const { id: theme, colorScheme, palette } of themes) it(`${theme} keeps semantic states distinct and readable on interactive surfaces`, () => {
  const css = read(`src/themes/${palette}`).toString()
  const tokens = Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[\da-f]+);/gi)].map(match => [match[1],match[2]]))
  const required = [...read(`src/themes/${resolveTheme('dark').palette}`).toString().matchAll(/--([\w-]+):/g)].map(match => match[1])
  const defined = [...css.matchAll(/--([\w-]+):/g)].map(match => match[1])
  expect(defined).toEqual(expect.arrayContaining(required))
  expect(css).toContain(`data-theme="${theme}"`)
  expect(css).toContain(`color-scheme: ${colorScheme};`)
  expect(tokens.success).toBeDefined()
  expect(new Set([tokens.accent,tokens.success,tokens.warning,tokens.error]).size).toBe(4)
  for (const foreground of ['text','muted','faint','accent']) {
    for (const background of ['background','sidebar','surface','raised','hover','input']) {
      expect(contrast(tokens[foreground],tokens[background]), `${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5)
    }
  }
  for (const state of ['accent','success','warning','error']) {
    expect(contrast(tokens[state],tokens[`${state}-surface`]),state).toBeGreaterThanOrEqual(4.5)
  }
  expect(contrast(tokens['accent-text'],tokens.accent)).toBeGreaterThanOrEqual(4.5)
  const brighten = (hex: string) => '#' + (hex.length === 4 ? [...hex.slice(1)].map(c => c+c) : hex.slice(1).match(/../g)!)
    .map(c => Math.min(255,Math.round(parseInt(c,16)*1.07)).toString(16).padStart(2,'0')).join('')
  expect(contrast(brighten(tokens['accent-text']),brighten(tokens.accent)), 'primary button hover').toBeGreaterThanOrEqual(4.5)
  // Aqua is an unchanged baseline; its original contrast checks remain above.
  for (const background of theme === 'dark' || theme === 'light' ? [] : ['group-surface','accent-surface']) {
    for (const foreground of ['text','muted','faint','accent']) {
      expect(contrast(tokens[foreground],tokens[background]), `${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5)
    }
  }
  if (theme !== 'dark' && theme !== 'light') {
    expect(defined).toEqual(expect.arrayContaining(['folder-icon','backdrop','popup-shadow','drag-shadow']))
    const surfaces = ['background','sidebar','surface','raised','hover','input','group-surface','accent-surface']
    for (const background of surfaces) {
      for (const foreground of ['success','warning','error','blue']) {
        expect(contrast(tokens[foreground],tokens[background]), `${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5)
      }
      expect(contrast(tokens.accent,tokens[background]), `focus on ${background}`).toBeGreaterThanOrEqual(3)
      expect(contrast(tokens['folder-icon'],tokens[background]), `folder icon on ${background}`).toBeGreaterThanOrEqual(3)
      const marked = blend(tokens['accent-surface'],tokens[background],.6)
      for (const foreground of ['text','muted','faint']) {
        expect(contrast(tokens[foreground],marked), `${foreground} on marked ${background}`).toBeGreaterThanOrEqual(4.5)
      }
    }
    expect(contrast(tokens.warning,blend(tokens.warning,tokens.surface,.08)), 'synchronization warning').toBeGreaterThanOrEqual(4.5)
  }
  for (const background of ['background','surface','hover','accent-surface']) {
    expect(contrast(tokens['accent-line'],tokens[background]), `selection outline on ${background}`).toBeGreaterThanOrEqual(3)
  }
  const styles = read('src/styles.css').toString()
  const excludedRule = styles.match(/\.file-table \.excluded \{([^}]*)\}/)?.[1] ?? ''
  const cutRule = styles.match(/\.file-table tr\[data-cut="true"\] \.file-name \{([^}]*)\}/)?.[1] ?? ''
  const excludedOpacity = Number(excludedRule.match(/opacity:\s*([\d.]+)/)?.[1] ?? 1)
  const cutOpacity = Number(cutRule.match(/opacity:\s*([\d.]+)/)?.[1] ?? 1)
  for (const background of ['surface','raised','hover']) {
    expect(contrast(tokens.faint,tokens[background],excludedOpacity), `excluded on ${background}`).toBeGreaterThanOrEqual(4.5)
    expect(contrast(tokens[cutRule.includes('--faint') ? 'faint' : 'text'],tokens[background],cutOpacity), `cut on ${background}`).toBeGreaterThanOrEqual(4.5)
    expect(contrast(tokens.faint,tokens[background],excludedOpacity * cutOpacity), `excluded and cut on ${background}`).toBeGreaterThanOrEqual(4.5)
  }
})
