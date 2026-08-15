import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { I18nContext } from './context'
import { messages } from './messages'
import type { I18nContextValue, Locale } from './types'

const STORAGE_KEY = 'sync-my-music:locale'
const SUPPORTED_LOCALES: Locale[] = ['en', 'pt-BR']

function isLocale(value: string | null): value is Locale {
  return Boolean(value && SUPPORTED_LOCALES.includes(value as Locale))
}

function detectLocale(): Locale {
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (isLocale(stored)) return stored
  return window.navigator.languages.some((language) => language.toLowerCase().startsWith('pt')) ? 'pt-BR' : 'en'
}

function interpolate(message: string, values?: Record<string, string | number>): string {
  if (!values) return message
  return message.replace(/\{(\w+)\}/g, (match, key: string) => String(values[key] ?? match))
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectLocale)

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  function setLocale(next: Locale) {
    window.localStorage.setItem(STORAGE_KEY, next)
    setLocaleState(next)
  }

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    setLocale,
    t(key, values) {
      return interpolate(messages[locale][key] ?? messages.en[key], values)
    },
  }), [locale])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
