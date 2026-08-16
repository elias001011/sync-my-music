import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { LuCircleAlert, LuTriangleAlert, LuX } from 'react-icons/lu'
import type { IconType } from 'react-icons'

import type { Account, SyncStatus } from '@/types'
import type { Translate } from '@/i18n/types'
import { useI18n } from '@/i18n/useI18n'

import { BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES, BUTTON_VARIANT_CLASSES } from '../ui/buttonStyles'

const STORAGE_KEY = 'songmirror-dismissed-alerts'

/** Enough held-back removals to see the pattern without turning a summary card
 * into a track listing; the remainder is reported as a count. */
const HELD_REMOVAL_PREVIEW = 6

/** Fewer than the held-removal preview: a failure line carries a whole error
 * message, and a pass that fails several playlists usually fails them all for the
 * same reason, which the first few already name. */
const FAILURE_PREVIEW = 4

interface NeedsLookItem {
  key: string
  icon: IconType
  title: string
  description: string
  /** Specifics behind the headline count — rendered verbatim, one line each. */
  details?: string[]
  action?: { label: string; to: string }
}

/** Every item here traces back to a real field — account state/detail, or the
 * last pass's own ok flag and per-target held/deferred/removals-skipped counts.
 * Nothing is invented (no fabricated "last synced" claims). */
function buildItems(accounts: Account[] | null, status: SyncStatus | null, t: Translate): NeedsLookItem[] {
  const items: NeedsLookItem[] = []

  for (const a of accounts ?? []) {
    if (a.state === 'expired') {
      items.push({
        key: `acct-${a.id}`,
        icon: LuTriangleAlert,
        title: t('dashboard.signInExpired', { name: a.name }),
        description: a.detail || t('dashboard.reconnectHelp'),
        action: { label: t('dashboard.reconnect'), to: '/accounts' },
      })
    } else if (a.state === 'error') {
      items.push({
        key: `acct-${a.id}`,
        icon: LuCircleAlert,
        title: t('dashboard.connectionError', { name: a.name }),
        description: a.detail || t('dashboard.connectionErrorHelp'),
        action: { label: t('dashboard.fix'), to: '/accounts' },
      })
    } else if (a.state === 'unconfigured') {
      items.push({
        key: `acct-${a.id}`,
        icon: LuTriangleAlert,
        title: t('dashboard.notSetUp', { name: a.name }),
        description: a.detail || t('dashboard.notSetUpHelp'),
        action: { label: t('dashboard.connect'), to: '/accounts' },
      })
    }
  }

  if (status?.last && !status.last.ok) {
    items.push({
      key: 'last-pass-error',
      icon: LuCircleAlert,
      title: t('dashboard.lastPassFailed'),
      description: status.last.error || t('dashboard.lastPassFailedHelp'),
    })
  }

  // A pass carries on past a playlist it can't sync, so `ok` stays true and this is
  // the only place that failure surfaces after the live feed has scrolled away.
  const failedTotal = status?.last?.per_target.reduce((sum, t) => sum + (t.failed ?? 0), 0) ?? 0
  if (failedTotal > 0) {
    const listed = status?.last?.per_target.flatMap((t) => t.failures ?? []) ?? []
    const details = listed.slice(0, FAILURE_PREVIEW).map((f) => `${f.playlist}: ${f.error}`)
    if (listed.length > details.length) {
      details.push(t('dashboard.more', { count: listed.length - details.length }))
    }
    items.push({
      key: 'playlists-failed',
      icon: LuCircleAlert,
      title: t(failedTotal === 1 ? 'dashboard.playlistFailed' : 'dashboard.playlistsFailed', { count: failedTotal }),
      description: t('dashboard.failedHelp'),
      details,
    })
  }

  const isrcFallback = status?.last?.per_target.reduce((sum, t) => sum + (t.isrc_fallback ?? 0), 0) ?? 0
  if (isrcFallback > 0) {
    items.push({
      key: 'isrc-fallback',
      icon: LuTriangleAlert,
      title: t(isrcFallback === 1 ? 'dashboard.isrcFallback' : 'dashboard.isrcFallbacks', { count: isrcFallback }),
      description: t('dashboard.isrcFallbackHelp'),
      action: { label: t('dashboard.connectApp'), to: '/accounts' },
    })
  }

  const heldTotal = status?.last?.per_target.reduce((sum, t) => sum + t.held + t.deferred, 0) ?? 0
  if (heldTotal > 0) {
    items.push({
      key: 'held',
      icon: LuTriangleAlert,
      title: t(heldTotal === 1 ? 'dashboard.changeHeld' : 'dashboard.changesHeld', { count: heldTotal }),
      description: t('dashboard.heldHelp'),
      action: { label: t('dashboard.reviewCaps'), to: '/sync' },
    })
  }

  const removalsSkipped = status?.last?.per_target.reduce((sum, t) => sum + (t.removals_skipped ?? 0), 0) ?? 0
  if (removalsSkipped > 0) {
    const listed = status?.last?.per_target.flatMap((t) => t.held_removals ?? []) ?? []
    // One "why" per distinct cause rather than repeated on every line — a pass
    // usually hits a single cause, and naming it is what makes the count actionable.
    const reasons = [...new Set(listed.map((h) => h.reason))]
    const details = listed
      .slice(0, HELD_REMOVAL_PREVIEW)
      .map((h) => `${h.track}${h.artist ? ` — ${h.artist}` : ''} · ${h.playlist} on ${h.target}`)
    if (listed.length > details.length) {
      details.push(t('dashboard.more', { count: listed.length - details.length }))
    }
    items.push({
      key: 'removals-skipped',
      icon: LuTriangleAlert,
      title: t(removalsSkipped === 1 ? 'dashboard.removalHeld' : 'dashboard.removalsHeld', { count: removalsSkipped }),
      description: reasons.length
        ? t('dashboard.removalsReasons', { reasons: reasons.join('; ') })
        : t('dashboard.removalsDefault'),
      details,
      action: { label: t('dashboard.openSync'), to: '/sync' },
    })
  }

  return items
}

/** Identity of an item's CURRENT state, not just its slot: the title and
 * description carry the counts, account name and error text, so a dismissal
 * only silences the exact situation the user saw. Two held removals dismissed,
 * then five held next pass -> a new signature, so it surfaces again. */
function signature(item: NeedsLookItem): string {
  return `${item.key}|${item.title}|${item.description}|${(item.details ?? []).join(',')}`
}

/** Never throws — an unavailable or corrupted store just means "nothing
 * dismissed" rather than a render crash (mirrors the live feed's persistence). */
function loadDismissed(): string[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const parsed: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(parsed) ? (parsed as string[]) : []
  } catch {
    try {
      window.localStorage?.removeItem(STORAGE_KEY)
    } catch {
      // Storage inaccessible even for a clear — nothing more we can do.
    }
    return []
  }
}

/** Real, actionable problems only — hidden entirely when there's nothing to
 * flag rather than showing an empty section. Each card can be dismissed; the
 * dismissal persists across reloads until that situation changes or clears. */
export function NeedsALook({ accounts, status }: { accounts: Account[] | null; status: SyncStatus | null }) {
  const { t } = useI18n()
  const [dismissed, setDismissed] = useState<string[]>(loadDismissed)
  const items = buildItems(accounts, status, t)
  const live = items.map(signature).join('\n')
  // Both sources must have answered before "this item is gone" means anything —
  // while they're still in flight there are no items at all, and pruning then
  // would drop every dismissal on each page load.
  const loaded = accounts !== null && status !== null

  // Forget dismissals whose situation is gone, so the store can't grow without
  // bound and a problem that recurs later is surfaced fresh rather than staying
  // silenced by a dismissal from weeks ago.
  useEffect(() => {
    if (!loaded) return
    const active = new Set(live ? live.split('\n') : [])
    setDismissed((prev) => {
      const next = prev.filter((k) => active.has(k))
      return next.length === prev.length ? prev : next
    })
  }, [loaded, live])

  useEffect(() => {
    try {
      window.localStorage?.setItem(STORAGE_KEY, JSON.stringify(dismissed))
    } catch {
      // Unavailable/full storage — dismissals just won't survive this reload.
    }
  }, [dismissed])

  const visible = items.filter((item) => !dismissed.includes(signature(item)))
  if (visible.length === 0) return null

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2.5">
        <h2 className="text-base font-extrabold tracking-tight text-text">{t('dashboard.needsLook')}</h2>
        <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-chip bg-warning-soft px-1.5 font-mono text-xs font-bold text-warning">
          {visible.length}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {visible.map((item) => (
          <div
            key={item.key}
            className="flex items-center gap-3 rounded-card border border-border border-l-[3px] border-l-warning bg-surface p-4 shadow-sm"
          >
            {/* Solid bg-warning + text-surface (not the soft-tinted bg-warning-soft
                text-warning used elsewhere), matching the app's other solid chips
                (e.g. the primary button's bg-accent text-on-accent) — an
                outline glyph in the same hue as a light fill is too
                low-contrast to read as anything but blank. --color-surface
                inverts appropriately per theme (light in light mode, dark in
                dark mode), so this stays legible against --color-warning's
                own per-theme fill in both. */}
            <span className="flex size-8 shrink-0 items-center justify-center rounded-control bg-warning text-surface">
              <item.icon className="size-[18px]" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold text-text">{item.title}</p>
              <p className="text-xs leading-relaxed text-text-2">{item.description}</p>
              {item.details && item.details.length > 0 && (
                <ul className="mt-1.5 flex flex-col gap-0.5">
                  {item.details.map((line) => (
                    <li key={line} className="truncate font-mono text-[11px] leading-relaxed text-text-3" title={line}>
                      {line}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {item.action && (
              <Link
                to={item.action.to}
                className={`${BUTTON_BASE_CLASSES} ${BUTTON_SIZE_CLASSES.sm} ${BUTTON_VARIANT_CLASSES.primary} shrink-0`}
              >
                {item.action.label}
              </Link>
            )}
            <button
              type="button"
              onClick={() => setDismissed((prev) => [...prev, signature(item)])}
              title={t('dashboard.dismiss')}
              aria-label={t('dashboard.dismissItem', { title: item.title })}
              className="flex size-7 shrink-0 items-center justify-center rounded-control text-text-3 transition-colors duration-fast hover:bg-surface-2 hover:text-text"
            >
              <LuX className="size-4" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
