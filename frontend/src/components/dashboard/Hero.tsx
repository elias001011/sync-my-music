import { LuCircleCheck, LuClock } from 'react-icons/lu'

import { formatDuration } from '@/lib/format'
import { cn } from '@/lib/cn'
import { localeTag, useI18n } from '@/i18n/useI18n'
import type { Locale, Translate } from '@/i18n/types'
import type { Account, SyncStatus } from '@/types'

interface HeroProps {
  accounts: Account[] | null
  status: SyncStatus | null
  /** Settings.DISPLAY_NAME — optional, user-set. Omitted from the greeting
   * entirely (not "Good evening, ") when blank. */
  displayName?: string
}

function timeOfDayGreeting(t: Translate): string {
  const h = new Date().getHours()
  if (h < 5) return t('dashboard.goodNight')
  if (h < 12) return t('dashboard.goodMorning')
  if (h < 18) return t('dashboard.goodAfternoon')
  return t('dashboard.goodEvening')
}

/** Plain-language summary of account health — never a fabricated "last
 * synced" claim, only what `useAccounts()` actually reports. */
function heroCopy(accounts: Account[] | null, t: Translate, locale: Locale): { headline: string; detail: string } {
  if (!accounts || accounts.length === 0) {
    return { headline: t('dashboard.nothingConnected'), detail: t('dashboard.connectToStart') }
  }
  const connected = accounts.filter((a) => a.state === 'connected')
  const problems = accounts.filter((a) => a.state !== 'connected')
  if (problems.length === 0) {
    return { headline: t('dashboard.everythingInSync'), detail: t('dashboard.allServicesUpToDate', { count: accounts.length }) }
  }
  if (connected.length === 0) {
    return { headline: t('dashboard.noneConnected'), detail: t('dashboard.connectToSync') }
  }
  const names = new Intl.ListFormat(localeTag(locale), { style: 'long', type: 'conjunction' }).format(problems.map((problem) => problem.name))
  return {
    headline: t('dashboard.almostInSync'),
    detail: t(problems.length === 1 ? 'dashboard.serviceNeedsAttention' : 'dashboard.servicesNeedAttention', {
      connected: connected.length,
      total: accounts.length,
      names,
    }),
  }
}

/** No per-pass "finished at" timestamp exists in the API (only how long the
 * pass took), so this reports what actually happened rather than a
 * fabricated "N min ago". A failed/preview pass may have no recorded
 * duration — formatDuration returns null for that, and the "· took …"
 * fragment is omitted entirely rather than printing a NaN-shaped string. */
function lastRunText(status: SyncStatus | null, t: Translate): string {
  if (!status?.last) return t('dashboard.noRunYet')
  const text = t(status.last.execute ? 'dashboard.lastApplied' : 'dashboard.lastPreview')
  const duration = formatDuration(status.last.duration_s)
  return duration ? t('dashboard.took', { text, duration }) : text
}

/** The dashboard's opening read: "how are things" in one sentence, framed by
 * a time-of-day greeting. Running state swaps to a compact live indicator —
 * there's no per-track progress signal in the API, so unlike the mockup this
 * never claims a fake "N of M checked" percentage. */
export function Hero({ accounts, status, displayName }: HeroProps) {
  const { locale, t } = useI18n()
  if (status?.running) {
    const previewing = status.mode === 'preview'
    const runningJobName = status.jobs.find((j) => j.id === status.running_job)?.name
    return (
      <div className="flex flex-1 flex-col justify-center gap-3">
        <span className="inline-flex items-center gap-2 font-mono text-[11px] font-bold tracking-[0.14em] text-accent">
          <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden="true" />
          {t(previewing ? 'dashboard.previewingNow' : 'dashboard.syncingNow')}
        </span>
        <h1 className="text-display text-[26px] text-text sm:text-[32px]">
          {previewing
            ? runningJobName
              ? t('dashboard.previewingJob', { name: runningJobName })
              : t('dashboard.previewingLibraries')
            : runningJobName
              ? t('dashboard.syncingJob', { name: runningJobName })
              : t('dashboard.syncingLibraries')}
        </h1>
        <p className="flex items-center gap-2 text-sm text-text-2">
          <LuClock className="size-4 shrink-0 text-text-3" aria-hidden="true" />
          {previewing
            ? t('dashboard.previewHelp')
            : t('dashboard.syncHelp')}
        </p>
      </div>
    )
  }

  const { headline, detail } = heroCopy(accounts, t, locale)
  const connectedCount = accounts?.filter((a) => a.state === 'connected').length ?? 0
  const allUp = Boolean(accounts?.length) && connectedCount === accounts?.length

  return (
    <div className="flex flex-1 flex-col justify-center gap-3.5">
      <span className="font-mono text-[11px] font-bold tracking-[0.14em] text-text-3">
        {timeOfDayGreeting(t).toUpperCase()}
        {displayName?.trim() ? `, ${displayName.trim().toUpperCase()}` : ''}
      </span>
      <h1 className="max-w-[16ch] text-[32px] font-extrabold leading-[1.05] tracking-tight text-text sm:text-[40px]">{headline}</h1>
      <p className="max-w-[52ch] text-[15px] leading-relaxed text-text-2">{detail}</p>
      <div className="mt-0.5 flex flex-wrap items-center gap-3 text-[13px] text-text-3">
        <span className="inline-flex items-center gap-1.5">
          <LuClock className="size-[15px] shrink-0" aria-hidden="true" />
          {lastRunText(status, t)}
        </span>
        {accounts && accounts.length > 0 && (
          <>
            <span className="size-1 shrink-0 rounded-full bg-border-strong" aria-hidden="true" />
            <span className={cn('inline-flex items-center gap-1.5', allUp && 'text-success')}>
              <LuCircleCheck className="size-[15px] shrink-0" aria-hidden="true" />
              {t('dashboard.upToDateCount', { connected: connectedCount, total: accounts.length })}
            </span>
          </>
        )}
      </div>
    </div>
  )
}
