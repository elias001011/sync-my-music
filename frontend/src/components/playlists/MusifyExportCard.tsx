import { useMemo, useState } from 'react'
import { LuExternalLink, LuSmartphone } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import type { ProviderPlaylistsEntry } from '@/hooks/useProviderPlaylists'
import type { Account } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { CopyButton } from '../ui/CopyButton'

export function MusifyExportCard({ accounts, entries }: { accounts: Account[]; entries: Record<string, ProviderPlaylistsEntry> }) {
  const sources = useMemo(() => accounts.filter((account) => account.state === 'connected' && account.capabilities?.playlist_read), [accounts])
  const [provider, setProvider] = useState('')
  const [playlist, setPlaylist] = useState('')
  const [link, setLink] = useState('')
  const [unmatched, setUnmatched] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const activeProvider = provider || sources[0]?.id || ''
  const playlists = entries[activeProvider]?.playlists ?? []

  async function generate() {
    setLoading(true); setError(null); setLink('')
    try {
      const result = await api.exportMusify({ source_provider: activeProvider, playlist_id: playlist })
      setLink(result.deep_link); setUnmatched(result.unmatched.length)
    } catch (err) { setError(errorMessage(err)) } finally { setLoading(false) }
  }

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-card bg-accent-soft text-accent"><LuSmartphone className="size-5" /></span><div><h2 className="text-[17px] font-bold text-text">Send a playlist to Musify</h2><p className="mt-1 text-sm text-text-3">Tracks are matched to YouTube IDs, then packed into a Musify custom-playlist link. Musify performs the final restore on your device.</p></div></div>
      <div className="mt-4 grid gap-3 md:grid-cols-[220px_1fr_auto]">
        <label className="flex flex-col gap-1.5 text-xs font-semibold text-text-2">Source service<select value={activeProvider} onChange={(e) => { setProvider(e.target.value); setPlaylist(''); setLink('') }} className="h-11 rounded-control border border-border-strong bg-field px-3 text-sm text-text">{sources.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label>
        <label className="flex flex-col gap-1.5 text-xs font-semibold text-text-2">Playlist<select value={playlist} onChange={(e) => setPlaylist(e.target.value)} className="h-11 rounded-control border border-border-strong bg-field px-3 text-sm text-text"><option value="">Choose a playlist…</option>{playlists.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <Button className="self-end" loading={loading} disabled={!activeProvider || !playlist} onClick={() => void generate()}>Generate link</Button>
      </div>
      {error && <p className="mt-3 rounded-control bg-danger-soft p-3 text-sm text-danger">{error}</p>}
      {link && <div className="mt-4 rounded-control border border-success bg-success-soft p-3"><div className="flex flex-wrap items-center gap-2"><code className="min-w-0 flex-1 truncate text-xs text-text">{link}</code><CopyButton value={link} label="Copy link" /><a href={link} className="inline-flex h-10 items-center gap-2 rounded-control bg-accent px-3 text-sm font-semibold text-on-accent"><LuExternalLink className="size-4" />Open Musify</a></div>{unmatched > 0 && <p className="mt-2 text-xs text-warning">{unmatched} tracks could not be matched and were left out. See Logs for details.</p>}</div>}
    </Card>
  )
}
