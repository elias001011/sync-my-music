import type { Account, SyncJob } from '@/types'

export function parseCsv(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

export function joinCsv(values: string[]): string {
  return values.join(',')
}

/** The stable value a job stores for one account: the backend's `account_id`
 * (`spotify:default`, `spotify:work`). The public `id` keeps the legacy bare
 * form (`spotify`) for old UIs, so job payloads must never use it as a value.
 * Falls back to `id` when the backend didn't send `account_id` (fixtures,
 * snapshot entries). */
export function accountStableId(account: Account): string {
  return account.account_id || account.id
}

/** Whether an id refers to `account`: its stable account id, or — for legacy
 * jobs saved with a bare `source="spotify"` — the public id. */
export function accountMatches(account: Account, id: string): boolean {
  return accountStableId(account) === id || account.id === id
}

/** The sync/transfer peers among `accounts`, in their original order. Keyed off
 * the backend's `transferable` flag (its targets registry is the single source
 * of truth), so browse-only services like Jellyfin — a connected account that
 * only receives pushed cover art — never appear as a Services/Providers toggle,
 * a Source-of-truth choice, or a transfer endpoint. */
export function syncPeersOf(accounts: Account[]): Account[] {
  return accounts.filter((a) => a.transferable)
}

/** Whichever peer is locked as the source in one-way mode — `null` in
 * N-way, which has no single source. */
export function lockedSourceOf(job: Pick<SyncJob, 'mode' | 'source'>): string | null {
  return job.mode === 'nway' ? null : job.source || 'spotify'
}

/** Which providers a job explicitly includes. Empty means none; treating it
 * as "every connected peer" made old jobs silently acquire newly connected
 * providers. The new-job wizard materializes its initial selection instead. */
export function enabledProvidersOf(job: Pick<SyncJob, 'providers'>, peers: Account[]): Set<string> {
  const explicit = parseCsv(job.providers)
  return new Set(explicit.filter((id) => peers.some((peer) => accountMatches(peer, id))))
}

export interface SyncSummaryRow {
  label: string
  value: string
}

/** Plain-English recap of a job's config, one labeled row per aspect —
 * shared by the wizard's final-step review (rendered as a structured
 * label→value layout) and the Sync list page's per-job summary line
 * (flattened, Schedule dropped since the card shows interval separately),
 * so the two surfaces can never describe the same job differently.
 * `downloadDir` is the *global* Settings value — only the wizard, which
 * reads it for display, passes it; the card's line stays path-free. */
export function buildSyncSummaryRows(job: SyncJob, peers: Account[], downloadDir?: string): SyncSummaryRow[] {
  const rows: SyncSummaryRow[] = []

  rows.push({ label: 'Schedule', value: job.enabled ? `Every ${job.interval || '?'}` : 'Manual' })

  const enabled = enabledProvidersOf(job, peers)
  const lockedId = lockedSourceOf(job)
  const enabledNames = peers
    .filter((a) => (lockedId ? accountMatches(a, lockedId) : false) || enabled.has(accountStableId(a)))
    .map((a) => a.name)
  if (job.mode === 'nway') {
    // No single source in N-way — just list who's included.
    const who = enabledNames.length > 0 ? enabledNames.join(' ⇄ ') : 'no services selected'
    rows.push({ label: 'Direction', value: `Bidirectional (N-way) · ${who}` })
  } else {
    const sourceName =
      peers.find((a) => accountMatches(a, job.source || 'spotify'))?.name ?? 'Spotify'
    const others = enabledNames.filter((n) => n !== sourceName)
    const who = others.length > 0 ? `${sourceName} → ${others.join(', ')}` : `${sourceName} only`
    rows.push({ label: 'Direction', value: `One-way · ${who}` })
  }

  const playlistNames = parseCsv(job.playlists)
  let playlistsValue: string
  if (playlistNames.length === 0) playlistsValue = 'All playlists'
  else if (playlistNames.length <= 3) playlistsValue = playlistNames.join(', ')
  else playlistsValue = `${playlistNames.slice(0, 3).join(', ')} +${playlistNames.length - 3} more`
  rows.push({ label: 'Playlists', value: playlistsValue })

  const removalNote = job.apply_large_removals ? ' (large removals drained in batches)' : ''
  rows.push({
    label: 'Limits',
    value:
      job.max_removals > 0
        ? `≤${job.max_adds} adds, ≤${job.max_removals} removals / pass${removalNote}`
        : `≤${job.max_adds} adds / pass · removals not mirrored`,
  })

  rows.push({
    label: 'Downloads',
    value: job.download ? (downloadDir?.trim() ? `On (${downloadDir.trim()})` : 'On') : 'Off',
  })

  return rows
}
