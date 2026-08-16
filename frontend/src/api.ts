// Thin typed fetch wrapper for the FastAPI backend. Same-origin in
// production (FastAPI serves the built SPA); proxied through Vite in dev
// (see vite.config.ts). No client-side base URL needed either way.
import type {
  Account,
  ConnectResponse,
  LinkUpsertRequest,
  LibrarySummary,
  LibraryAccount,
  LibraryTracksResponse,
  MusifyExportResponse,
  MusifyBackupImport,
  OkResponse,
  PlaylistLink,
  PlaylistRestorePlan,
  PlaylistVersion,
  PollResponse,
  ProviderPlaylist,
  ResolveConflictRequest,
  Recap,
  RecapHistory,
  RunResponse,
  ScheduleRequest,
  SonoraStatus,
  SpotifyExportImport,
  Settings,
  StartTransferRequest,
  StartTransferResponse,
  SyncJob,
  SyncJobUpsertRequest,
  SyncStatus,
  SystemBackupRestore,
  TransferControlResponse,
  TransferJob,
} from './types'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      headers: init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
    })
  } catch {
    throw new ApiError(0, 'Could not reach the server. Check that it is running and reachable.')
  }

  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`
    try {
      const body: unknown = await res.clone().json()
      if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
        detail = body.detail
      }
    } catch {
      // Response wasn't JSON — fall back to the status text above.
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  const text = await res.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const api = {
  // Canonical local library + unified listening history
  getLibrarySummary: () => request<LibrarySummary>('/api/library/summary'),
  getLibraryAccounts: () => request<LibraryAccount[]>('/api/library/accounts'),
  getLibraryTracks: (q = '', limit = 100, offset = 0) =>
    request<LibraryTracksResponse>(`/api/library/tracks?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`),
  getRecap: (year: number, month?: number, accounts?: string[]) =>
    request<Recap>(`/api/recaps?year=${year}${month ? `&month=${month}` : ''}${accounts?.length ? `&accounts=${accounts.map(encodeURIComponent).join(',')}` : ''}`),
  getRecapHistory: (accounts?: string[]) =>
    request<RecapHistory>(`/api/recaps/history${accounts?.length ? `?accounts=${accounts.map(encodeURIComponent).join(',')}` : ''}`),
  getLogs: (kind = '', tag = '', q = '', limit = 500) =>
    request<import('./types').SyncEvent[]>(`/api/logs?kind=${encodeURIComponent(kind)}&tag=${encodeURIComponent(tag)}&q=${encodeURIComponent(q)}&limit=${limit}`),
  exportMusify: (body: { source_provider: string; playlist_id: string; title?: string }) =>
    request<MusifyExportResponse>('/api/musify/export', json(body)),
  restoreMusifyBackup: (file: File, label = 'Musify backup') => {
    const form = new FormData()
    form.append('backup', file)
    form.append('label', label)
    return request<MusifyBackupImport>('/api/musify/backup', { method: 'POST', body: form })
  },
  restoreSpotifyExport: (file: File, label: string) => {
    const form = new FormData()
    form.append('backup', file)
    form.append('label', label)
    return request<SpotifyExportImport>('/api/spotify/export-import', { method: 'POST', body: form })
  },
  restoreAccountBackup: (file: File, accountId?: string) => {
    const form = new FormData()
    form.append('backup', file)
    if (accountId) form.append('account_id', accountId)
    return request<{ account_id: string; provider: string; label: string; tracks: number; playlists: number; listens: number }>(
      '/api/library/account-backup', { method: 'POST', body: form })
  },
  renameLibraryAccount: (id: string, label: string) =>
    request<{ id: string; label: string }>(`/api/library/accounts/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ label }) }),
  /** Destructive — the backend refuses the call unless `confirm` is set. */
  deleteLibraryAccount: (id: string) =>
    request<{ account_id: string; deleted: true }>(`/api/library/accounts/${encodeURIComponent(id)}`, { method: 'DELETE', body: JSON.stringify({ confirm: true }) }),
  getSonoraStatus: () => request<SonoraStatus>('/api/sonora/status'),
  setSonoraEnabled: (enabled: boolean) => request<SonoraStatus>('/api/sonora/status', { method: 'PUT', body: JSON.stringify({ enabled }) }),
  discoverSonora: () => request<Array<{ device_id: string; name: string; ip: string; port: number }>>('/api/sonora/discover', { method: 'POST' }),
  requestSonoraPair: (ip: string, port: number) => request<{ status: string }>('/api/sonora/pair-request', json({ ip, port })),
  verifySonoraPair: (ip: string, port: number, pin: string) => request<{ status: string }>('/api/sonora/pair-verify', json({ ip, port, pin })),
  syncSonora: (deviceId: string, surfaces: string[]) => request<{ local: Record<string, number>; remote: Record<string, number> }>(`/api/sonora/devices/${encodeURIComponent(deviceId)}/sync`, json({ surfaces })),
  restoreSonoraBackup: (file: File) => {
    const form = new FormData()
    form.append('backup', file)
    return request<Record<string, number>>('/api/sonora/backup', { method: 'POST', body: form })
  },

  // Accounts
  getAccounts: () => request<Account[]>('/api/accounts'),
  saveAccountConfig: (id: string, values: Record<string, string>) =>
    request<OkResponse>(`/api/accounts/${id}/config`, json(values)),
  connectAccount: (id: string, values?: Record<string, string>) =>
    request<ConnectResponse>(`/api/accounts/${id}/connect`, { method: 'POST', ...(values ? { body: JSON.stringify(values) } : {}) }),
  pollAccount: (id: string, deviceCode: string, interval: number) =>
    request<PollResponse>(`/api/accounts/${id}/poll`, json({ device_code: deviceCode, interval })),
  disconnectAccount: (id: string) => request<OkResponse>(`/api/accounts/${id}`, { method: 'DELETE' }),
  setAccountEnabled: (id: string, enabled: boolean) =>
    request<OkResponse>(`/api/accounts/${id}/enabled`, { method: 'PUT', body: JSON.stringify({ enabled }) }),
  /** Per-account switches: pause the whole profile or individual surfaces. */
  setAccountPrefs: (id: string, prefs: { enabled?: boolean; surfaces?: Partial<Record<import('./types').SurfaceName, boolean>> }) =>
    request<{ ok: true; account_id: string; enabled: boolean; surfaces: Record<string, boolean> }>(`/api/accounts/${id}/prefs`, { method: 'PUT', body: JSON.stringify(prefs) }),
  /** YouTube Music-only "no-quota" mode: routes reads/writes through a pasted
   * browser session instead of the (daily-capped) Data API. `headers` is the
   * raw "copy request headers" block from a music.youtube.com XHR. */
  enableYtmusicBrowserMode: (headers: string) => request<PollResponse>('/api/accounts/ytmusic/browser', json({ headers })),
  disableYtmusicBrowserMode: () => request<PollResponse>('/api/accounts/ytmusic/browser', { method: 'DELETE' }),
  /** Spotify Web/cookie mode: first-party session for listing, search and writes,
   * with no OAuth developer app dependency. */
  enableSpotifyCookieMode: (spDc: string) => request<PollResponse>('/api/accounts/spotify/cookie', json({ sp_dc: spDc })),
  disableSpotifyCookieMode: () => request<PollResponse>('/api/accounts/spotify/cookie', { method: 'DELETE' }),

  /** A second Spotify app (Extended Quota Mode) used only for ISRC /tracks lookups —
   * a rate bucket separate from the OAuth user token, needed for reliable N-way matching. */
  setSpotifyIsrcApp: (clientId: string, clientSecret: string) =>
    request<PollResponse>('/api/accounts/spotify/isrc-app', json({ client_id: clientId, client_secret: clientSecret })),
  clearSpotifyIsrcApp: () => request<PollResponse>('/api/accounts/spotify/isrc-app', { method: 'DELETE' }),

  // Settings
  getSettings: () => request<Settings>('/api/settings'),
  saveSettings: (values: Settings) => request<OkResponse>('/api/settings', { method: 'PUT', body: JSON.stringify(values) }),
  restoreSystemBackup: (file: File) => {
    const form = new FormData()
    form.append('backup', file)
    return request<SystemBackupRestore>('/api/system-backup/restore', { method: 'POST', body: form })
  },

  // Sync (global: run-all + the auto-sync master switch)
  runSync: (execute: boolean) => request<RunResponse>(`/api/sync/run?execute=${execute ? 1 : 0}`, { method: 'POST' }),
  getSyncStatus: () => request<SyncStatus>('/api/sync/status'),
  setSchedule: (body: ScheduleRequest) => request<SyncStatus>('/api/sync/schedule', json(body)),

  // Sync jobs (named, multiple — each an independent sync configuration)
  getSyncs: () => request<SyncJob[]>('/api/syncs'),
  createSync: (values: SyncJobUpsertRequest) => request<SyncJob>('/api/syncs', json(values)),
  updateSync: (id: string, values: SyncJobUpsertRequest) =>
    request<SyncJob>(`/api/syncs/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(values) }),
  deleteSync: (id: string) => request<OkResponse>(`/api/syncs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  runSyncJob: (id: string, execute: boolean) =>
    request<RunResponse>(`/api/syncs/${encodeURIComponent(id)}/run?execute=${execute ? 1 : 0}`, { method: 'POST' }),
  pauseSyncJob: (id: string) => request<OkResponse>(`/api/syncs/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  stopSyncJob: (id: string) => request<OkResponse>(`/api/syncs/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  resumeSyncJob: (id: string) => request<OkResponse>(`/api/syncs/${encodeURIComponent(id)}/resume`, { method: 'POST' }),

  // Playlists (browse)
  getPlaylists: (provider: string) =>
    request<ProviderPlaylist[]>(`/api/playlists?provider=${encodeURIComponent(provider)}`),
  getPlaylistVersions: (provider: string, playlistId: string) =>
    request<PlaylistVersion[]>(`/api/playlist-versions?provider=${encodeURIComponent(provider)}&playlist_id=${encodeURIComponent(playlistId)}`),
  restorePlaylistVersion: (provider: string, playlistId: string, capturedAt: string, execute: boolean, maxRemovals = 100) =>
    request<PlaylistRestorePlan>('/api/playlist-versions/restore', json({ provider, playlist_id: playlistId, captured_at: capturedAt, execute, max_removals: maxRemovals })),

  // Links (cross-service pairings)
  getLinks: () => request<PlaylistLink[]>('/api/links'),
  upsertLink: (link: LinkUpsertRequest) => request<PlaylistLink>('/api/links', { method: 'PUT', body: JSON.stringify(link) }),
  deleteLink: (id: string) => request<OkResponse>(`/api/links/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // Transfers (one-off playlist copy)
  startTransfer: (body: StartTransferRequest) => request<StartTransferResponse>('/api/transfers', json(body)),
  getTransfer: (id: string) => request<TransferJob>(`/api/transfers/${encodeURIComponent(id)}`),
  /** Active jobs only (queued/running/paused) — the dashboard's "Ongoing
   * transfers" list. */
  listTransfers: () => request<TransferJob[]>('/api/transfers'),
  pauseTransfer: (id: string) => request<TransferControlResponse>(`/api/transfers/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  resumeTransfer: (id: string) => request<TransferControlResponse>(`/api/transfers/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  stopTransfer: (id: string) => request<TransferControlResponse>(`/api/transfers/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  resolveTransferConflict: (id: string, body: ResolveConflictRequest) =>
    request<OkResponse>(`/api/transfers/${encodeURIComponent(id)}/resolve`, json(body)),
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return String(err)
}
