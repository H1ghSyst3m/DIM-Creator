import type { de } from './de'

export type CatalogEntry = string | ({ other: string } & Partial<Record<Intl.LDMLPluralRule, string>>)
export type Message = keyof typeof de
export type Catalog = Record<Message, CatalogEntry>
export type Translate = (message: Message, values?: Record<string, string | number>) => string
export type Language = { id: string; name: string; locale: string; catalog: Catalog }
