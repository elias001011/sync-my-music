import type { MessageKey } from './messages'

export type Locale = 'en' | 'pt-BR'

export interface I18nContextValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: MessageKey, values?: Record<string, string | number>) => string
}
