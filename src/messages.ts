import type { AppError } from './bindings'
import { de } from './locales/de'
import { en } from './locales/en'
import type { Language, Message, Translate } from './locales/types'
export type { Message, Translate } from './locales/types'

export const languages: readonly Language[] = [
  { id: 'de', name: 'Deutsch', locale: 'de', catalog: de },
  { id: 'en', name: 'English', locale: 'en', catalog: en },
]

export function resolveLanguage(id: string, registry = languages): Language {
  return registry.find(language => language.id === id) ?? registry[0]
}

export function createTranslator(language: Language): Translate {
  const rules = new Intl.PluralRules(language.locale)
  return (message, values = {}) => {
    const entry = language.catalog[message]
    const text = typeof entry === 'string' ? entry : entry[rules.select(Number(values.count))] ?? entry.other
    return text.replace(/\{(\w+)\}/g, (match, key: string) => String(values[key] ?? match))
  }
}

export function translator(language: string): Translate {
  return createTranslator(resolveLanguage(language))
}

export function isMessage(key: string): key is Message {
  return Object.hasOwn(de, key)
}

export function appError(code: string, params: Record<string, string> = {}, causes: AppError[] = []): AppError {
  return { code, params, details: null, causes }
}

export function errorText(error: unknown, t: Translate): string {
  if (error && typeof error === 'object' && 'code' in error && typeof error.code === 'string') {
    const failure = error as AppError
    const template = isMessage(failure.code) ? t(failure.code) : t('operationError')
    const label = isMessage(failure.code) ? t(failure.code, failure.params) : t('operationError')
    const context = Object.entries(failure.params ?? {}).filter(([key]) =>
      ['path', 'name', 'value', 'id'].includes(key) && !template.includes(`{${key}}`)).map(([, value]) => value)
    return [label, ...context, failure.details, ...(failure.causes ?? []).map(cause => errorText(cause, t))].filter(Boolean).join(' ')
  }
  const detail = error instanceof Error ? error.message : typeof error === 'string' ? error : ''
  return isMessage(detail) ? t(detail) : [t('operationError'), detail].filter(Boolean).join(' ')
}

export const issueText = errorText

export function sizeText(bytes: number, language: string) {
  const scale = bytes >= 1024 ** 3 ? 3 : bytes >= 1024 ** 2 ? 2 : bytes >= 1024 ? 1 : 0
  const number = new Intl.NumberFormat(resolveLanguage(language).locale, { maximumFractionDigits: scale ? 1 : 0 }).format(bytes / 1024 ** scale)
  return number + ' ' + ['B', 'KiB', 'MiB', 'GiB'][scale]
}
