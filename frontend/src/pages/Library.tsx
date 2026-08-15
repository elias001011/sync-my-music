import { useEffect, useState } from 'react'
import { LuAlbum, LuClock3, LuDisc3, LuMusic2, LuSearch, LuUsers } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { Card } from '@/components/ui/Card'
import type { LibrarySummary, LibraryTrack } from '@/types'

function duration(ms: number | null) {
  if (!ms) return '—'
  const seconds = Math.round(ms / 1000)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export default function Library() {
  const [summary, setSummary] = useState<LibrarySummary | null>(null)
  const [tracks, setTracks] = useState<LibraryTrack[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      Promise.all([api.getLibrarySummary(), api.getLibraryTracks(query)])
        .then(([nextSummary, response]) => {
          setSummary(nextSummary)
          setTracks(response.items)
          setTotal(response.total)
          setError(null)
        })
        .catch((err: unknown) => setError(errorMessage(err)))
    }, query ? 180 : 0)
    return () => window.clearTimeout(timer)
  }, [query])

  const stats = [
    { label: 'Tracks', value: summary?.tracks ?? 0, icon: LuMusic2 },
    { label: 'Artists', value: summary?.artists ?? 0, icon: LuUsers },
    { label: 'Albums', value: summary?.albums ?? 0, icon: LuAlbum },
    { label: 'Playlists', value: summary?.playlists ?? 0, icon: LuDisc3 },
    { label: 'Listens', value: summary?.listens ?? 0, icon: LuClock3 },
  ]

  return (
    <div className="flex flex-col gap-6">
      <div>
        <span className="font-mono text-[10px] font-bold tracking-[0.14em] text-accent">CANONICAL DATABASE</span>
        <h1 className="mt-1 text-xl font-bold tracking-tight text-text sm:text-[22px]">Your music, independent of any service</h1>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-text-3">
          One local identity for every track, linked to its Spotify, YouTube Music, Amazon, Musify and Sonora versions.
          Provider libraries are mirrors; this database is the durable source of truth.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {stats.map((stat) => (
          <Card key={stat.label} className="flex items-center gap-3 p-4">
            <span className="grid size-9 place-items-center rounded-control bg-accent-soft text-accent"><stat.icon className="size-4" /></span>
            <div><strong className="block text-lg text-text">{stat.value.toLocaleString()}</strong><span className="text-xs text-text-3">{stat.label}</span></div>
          </Card>
        ))}
      </div>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-border p-4">
          <div className="relative min-w-56 flex-1">
            <LuSearch className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-3" />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search track, artist or album"
              className="h-10 w-full rounded-control border border-border bg-field pl-9 pr-3 text-sm text-text" />
          </div>
          <span className="font-mono text-[11px] text-text-3">{total.toLocaleString()} MATCHES</span>
        </div>
        {error ? <p className="m-4 rounded-control bg-danger-soft p-3 text-sm text-danger">{error}</p> : tracks.length === 0 ? (
          <div className="px-5 py-14 text-center"><p className="font-bold text-text">The canonical library is empty</p><p className="mt-1 text-sm text-text-3">Connect a service, import a Sonora backup, or send listening events to begin.</p></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="bg-surface-2 font-mono text-[10px] uppercase tracking-wider text-text-3"><tr><th className="px-4 py-3">Track</th><th className="px-4 py-3">Album</th><th className="px-4 py-3 text-right">Plays</th><th className="px-4 py-3 text-right">Length</th><th className="px-4 py-3">ISRC</th></tr></thead>
              <tbody>{tracks.map((track) => <tr key={track.id} className="border-t border-border"><td className="px-4 py-3"><strong className="block text-text">{track.title}</strong><span className="text-xs text-text-3">{track.artist}</span></td><td className="px-4 py-3 text-text-2">{track.album || '—'}</td><td className="px-4 py-3 text-right font-mono text-text-2">{track.play_count}</td><td className="px-4 py-3 text-right font-mono text-text-3">{duration(track.duration_ms)}</td><td className="px-4 py-3 font-mono text-[11px] text-text-3">{track.isrc || '—'}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
