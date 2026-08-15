import { useMemo, useState } from 'react'
import { LuHistory, LuRotateCcw } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import type { ProviderPlaylistsEntry } from '@/hooks/useProviderPlaylists'
import type { Account, PlaylistRestorePlan, PlaylistVersion } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

export function PlaylistVersionCard({ accounts, entries }: { accounts: Account[]; entries: Record<string, ProviderPlaylistsEntry> }) {
  const sources = useMemo(() => accounts.filter((a) => a.state === 'connected' && a.enabled !== false && (a.capabilities?.playlist_write || a.id === 'musify')), [accounts])
  const [provider, setProvider] = useState('')
  const [playlist, setPlaylist] = useState('')
  const [versions, setVersions] = useState<PlaylistVersion[]>([])
  const [selected, setSelected] = useState('')
  const [plan, setPlan] = useState<PlaylistRestorePlan | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const activeProvider = provider || sources[0]?.id || ''
  const playlists = entries[activeProvider]?.playlists ?? []

  async function loadVersions() {
    setBusy('load'); setError(null); setPlan(null)
    try { const rows = await api.getPlaylistVersions(activeProvider, playlist); setVersions(rows); setSelected(rows[0]?.version_id ?? rows[0]?.captured_at ?? '') } catch (err) { setError(errorMessage(err)) } finally { setBusy('') }
  }
  async function restore(execute: boolean) {
    setBusy(execute ? 'restore' : 'preview'); setError(null)
    try { const result = await api.restorePlaylistVersion(activeProvider, playlist, selected, execute); setPlan(result); if (execute) await loadVersions() } catch (err) { setError(errorMessage(err)) } finally { setBusy('') }
  }

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-card bg-info-soft text-info"><LuHistory className="size-5" /></span><div><h2 className="text-[17px] font-bold text-text">Playlist version recovery</h2><p className="mt-1 text-sm text-text-3">Every observed change keeps a bounded snapshot. Preview the membership difference before restoring; recovery never wipes a playlist just to reorder it.</p></div></div>
      <div className="mt-4 grid gap-3 md:grid-cols-[200px_1fr_auto]">
        <select value={activeProvider} onChange={(e) => { setProvider(e.target.value); setPlaylist(''); setVersions([]); setPlan(null) }} className="h-11 rounded-control border border-border-strong bg-field px-3 text-sm text-text">{sources.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select>
        <select value={playlist} onChange={(e) => { setPlaylist(e.target.value); setVersions([]); setPlan(null) }} className="h-11 rounded-control border border-border-strong bg-field px-3 text-sm text-text"><option value="">Choose a playlist…</option>{playlists.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        <Button variant="secondary" loading={busy === 'load'} disabled={!playlist} onClick={() => void loadVersions()}>Load versions</Button>
      </div>
      {versions.length > 0 && <div className="mt-3 flex flex-wrap items-end gap-3"><label className="min-w-64 flex-1 text-xs font-semibold text-text-2">Snapshot<select value={selected} onChange={(e) => { setSelected(e.target.value); setPlan(null) }} className="mt-1.5 h-11 w-full rounded-control border border-border-strong bg-field px-3 text-sm text-text">{versions.map((version) => <option key={version.version_id ?? version.captured_at} value={version.version_id ?? version.captured_at}>{new Date(version.captured_at).toLocaleString()} · {version.item_count} tracks</option>)}</select></label><Button loading={busy === 'preview'} onClick={() => void restore(false)}>Preview restore</Button></div>}
      {!busy && playlist && versions.length === 0 && !error && <p className="mt-3 rounded-control bg-inset p-3 text-xs text-text-3">No snapshots loaded. A playlist gets its first version when a preview/sync observes it.</p>}
      {plan && <div className="mt-3 flex flex-wrap items-center gap-3 rounded-control border border-warning bg-warning-soft p-3"><div className="min-w-0 flex-1"><strong className="text-sm text-text">Restore plan: +{plan.additions} / −{plan.removals}</strong><p className="text-xs text-text-3">Historical membership: {plan.target_count} tracks · {plan.order_note}.</p></div>{!plan.execute && <Button icon={<LuRotateCcw className="size-4" />} loading={busy === 'restore'} disabled={plan.additions === 0 && plan.removals === 0} onClick={() => void restore(true)}>Apply restore</Button>}</div>}
      {error && <p className="mt-3 rounded-control bg-danger-soft p-3 text-sm text-danger">{error}</p>}
    </Card>
  )
}
