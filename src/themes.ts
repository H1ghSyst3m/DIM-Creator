import type { Message, Translate } from './messages'
import './themes/classic/dark/aqua.css'
import './themes/classic/light/aqua.css'
import './themes/classic/dark/graphite.css'
import './themes/classic/light/polar.css'
import './themes/classic/dark/evergreen.css'
import './themes/classic/light/sand.css'
import './themes/glass/light/frost.css'
import './themes/glass/dark/nocturne.css'
import './themes/glass/material.css'

export type Theme = { id: string; label: Message; category: Message; colorScheme: 'dark' | 'light'; palette: string }
export const themes: readonly Theme[] = [
  { id: 'dark', label: 'aqua', category: 'classic', colorScheme: 'dark', palette: 'classic/dark/aqua.css' },
  { id: 'light', label: 'aqua', category: 'classic', colorScheme: 'light', palette: 'classic/light/aqua.css' },
  { id: 'graphite', label: 'graphite', category: 'classic', colorScheme: 'dark', palette: 'classic/dark/graphite.css' },
  { id: 'polar', label: 'polar', category: 'classic', colorScheme: 'light', palette: 'classic/light/polar.css' },
  { id: 'evergreen', label: 'evergreen', category: 'classic', colorScheme: 'dark', palette: 'classic/dark/evergreen.css' },
  { id: 'sand', label: 'sand', category: 'classic', colorScheme: 'light', palette: 'classic/light/sand.css' },
  { id: 'frost', label: 'frost', category: 'glass', colorScheme: 'light', palette: 'glass/light/frost.css' },
  { id: 'nocturne', label: 'nocturne', category: 'glass', colorScheme: 'dark', palette: 'glass/dark/nocturne.css' },
]
export function resolveTheme(id: string, registry = themes): Theme {
  return registry.find(theme => theme.id === id) ?? registry[0]
}
export function themeGroups(registry = themes) {
  return [...new Set(registry.map(theme => theme.category))].map(category => ({category,
    schemes: (['light','dark'] as const).map(colorScheme => ({colorScheme,
      themes: registry.filter(theme => theme.category === category && theme.colorScheme === colorScheme),
    })).filter(group => group.themes.length > 0),
  }))
}
export function themeLabel(theme: Theme, t: Translate) {
  return `${t(theme.label)} · ${t(theme.colorScheme)}`
}
