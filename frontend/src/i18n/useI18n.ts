import { useContext } from 'react'

import { I18nContext } from './context'
import type { I18nContextValue, Locale } from './types'

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext)
  if (!context) throw new Error('useI18n must be used inside I18nProvider')
  return context
}

export function localeTag(locale: Locale): string {
  return locale === 'pt-BR' ? 'pt-BR' : 'en-US'
}
