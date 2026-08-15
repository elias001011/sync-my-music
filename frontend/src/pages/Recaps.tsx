import { useEffect, useMemo, useState } from 'react'
import { LuClock3, LuHeadphones, LuMusic2, LuUsers } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { Card } from '@/components/ui/Card'
import type { Recap } from '@/types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export default function Recaps() {
  const [year, setYear] = useState(new Date().getFullYear())
  const [recap, setRecap] = useState<Recap | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.getRecap(year).then(setRecap).catch((err: unknown) => setError(errorMessage(err))) }, [year])
  const monthly = useMemo(() => MONTHS.map((label, index) => ({ label, plays: recap?.by_month.find((m) => m.month === index + 1)?.plays ?? 0 })), [recap])
  const max = Math.max(1, ...monthly.map((item) => item.plays))

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div><span className="font-mono text-[10px] font-bold tracking-[0.14em] text-accent">ALL SERVICES · ONE STORY</span><h1 className="mt-1 text-xl font-bold tracking-tight text-text sm:text-[22px]">Listening recap</h1><p className="mt-1 text-sm text-text-3">Spotify, YouTube Music, Amazon, Musify and Sonora listens combined and deduplicated.</p></div>
        <select value={year} onChange={(event) => setYear(Number(event.target.value))} className="h-10 rounded-control border border-border bg-field px-3 text-sm text-text">
          {Array.from({ length: 8 }, (_, i) => new Date().getFullYear() - i).map((item) => <option key={item}>{item}</option>)}
        </select>
      </div>
      {error && <p className="rounded-control bg-danger-soft p-3 text-sm text-danger">{error}</p>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[{ label: 'Plays', value: recap?.plays ?? 0, icon: LuHeadphones }, { label: 'Minutes', value: Math.round((recap?.listened_ms ?? 0) / 60000), icon: LuClock3 }, { label: 'Unique tracks', value: recap?.tracks ?? 0, icon: LuMusic2 }, { label: 'Artists', value: recap?.artists ?? 0, icon: LuUsers }].map((stat) => <Card key={stat.label} className="p-4"><stat.icon className="mb-4 size-4 text-accent" /><strong className="block text-2xl text-text">{stat.value.toLocaleString()}</strong><span className="text-xs text-text-3">{stat.label}</span></Card>)}
      </div>
      <Card className="p-4 sm:p-5"><h2 className="text-sm font-bold text-text">Listening rhythm</h2><div className="mt-6 flex h-44 items-end gap-2">{monthly.map((item) => <div key={item.label} className="flex h-full flex-1 flex-col items-center justify-end gap-2"><span className="font-mono text-[9px] text-text-3">{item.plays || ''}</span><div className="w-full max-w-10 rounded-t-chip bg-accent" style={{ height: `${Math.max(item.plays ? 8 : 2, item.plays / max * 100)}%` }} /><span className="font-mono text-[9px] text-text-3">{item.label}</span></div>)}</div></Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4 sm:p-5"><h2 className="mb-3 text-sm font-bold text-text">Top tracks</h2>{recap?.top_tracks.length ? <ol className="divide-y divide-border">{recap.top_tracks.map((track, index) => <li key={track.id} className="flex items-center gap-3 py-3"><span className="w-5 font-mono text-xs text-text-3">{index + 1}</span><div className="min-w-0 flex-1"><strong className="block truncate text-sm text-text">{track.title}</strong><span className="text-xs text-text-3">{track.artist}</span></div><span className="font-mono text-xs text-text-2">{track.plays} plays</span></li>)}</ol> : <p className="py-10 text-center text-sm text-text-3">No listening data for {year} yet.</p>}</Card>
        <Card className="p-4 sm:p-5"><h2 className="mb-3 text-sm font-bold text-text">Services</h2>{recap?.services.length ? <div className="flex flex-col gap-3">{recap.services.map((service) => <div key={service.source}><div className="mb-1 flex justify-between text-xs"><span className="capitalize text-text-2">{service.source}</span><span className="font-mono text-text-3">{service.plays} plays</span></div><div className="h-2 overflow-hidden rounded-full bg-inset"><div className="h-full rounded-full bg-accent" style={{ width: `${service.plays / Math.max(1, recap.plays) * 100}%` }} /></div></div>)}</div> : <p className="py-10 text-center text-sm text-text-3">Sources appear here as listening events arrive.</p>}</Card>
      </div>
    </div>
  )
}
