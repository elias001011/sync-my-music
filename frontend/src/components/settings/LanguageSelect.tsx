import type { Locale } from '@/i18n/types'
import { useI18n } from '@/i18n/useI18n'
import { cn } from '@/lib/cn'

export function LanguageSelect() {
  const { locale, setLocale, t } = useI18n()

  return (
    <label className="flex flex-col gap-1.5 text-[12.5px] font-semibold text-text-2">
      {t('settings.language')}
      <select
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
        className={cn('h-10 rounded-control border border-border-strong bg-field px-3 text-sm font-normal text-text')}
      >
        <option value="en">{t('language.english')}</option>
        <option value="pt-BR">{t('language.portugueseBrazil')}</option>
      </select>
      <span className="text-xs font-normal leading-relaxed text-text-3">{t('settings.languageHelp')}</span>
    </label>
  )
}
