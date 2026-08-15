import { useEffect, useMemo, useState } from 'react'
import { LuCircle, LuSearch, LuTrash2 } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { EventFeedList } from '@/components/events/EventFeedList'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { useEventStream } from '@/hooks/useEventStream'
import type { SyncEvent } from '@/types'

export default function Logs() {
  const live = useEventStream()
  const [stored, setStored] = useState<SyncEvent[]>([])
  const [kind, setKind] = useState('')
  const [tag, setTag] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      api.getLogs(kind, tag, query).then((rows) => { setStored(rows.reverse()); setError(null) }).catch((err: unknown) => setError(errorMessage(err)))
    }, query ? 180 : 0)
    return () => window.clearTimeout(timer)
  }, [kind, tag, query])

  const events = useMemo(() => {
    const cutoff = stored.at(-1)?.ts ?? 0
    return [...stored, ...live.events.filter((event) => event.ts > cutoff)]
  }, [stored, live.events])
  const tags = useMemo(() => Array.from(new Set(events.map((event) => event.tag).filter(Boolean))).sort(), [events])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-bold tracking-tight text-text sm:text-[22px]">Application logs</h1><p className="mt-1 text-sm text-text-3">Persistent, searchable diagnostics for each connector and synchronization run.</p></div>
        <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 px-3 py-1.5 text-xs text-text-2"><LuCircle className={`size-2 fill-current ${live.connected ? 'text-success' : 'text-danger'}`} />{live.connected ? 'Live stream connected' : 'Live stream reconnecting'}</span>
      </div>
      <Card className="p-3 sm:p-4">
        <div className="grid gap-3 md:grid-cols-[1fr_180px_180px_auto]">
          <label className="relative"><span className="sr-only">Search logs</span><LuSearch className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-3" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search messages" className="h-10 w-full rounded-control border border-border bg-field pl-9 pr-3 text-sm text-text" /></label>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="h-10 rounded-control border border-border bg-field px-3 text-sm text-text"><option value="">All severities</option>{['warn', 'miss', 'hold', 'add', 'remove', 'summary', 'section', 'note', 'download'].map((value) => <option key={value}>{value}</option>)}</select>
          <select value={tag} onChange={(e) => setTag(e.target.value)} className="h-10 rounded-control border border-border bg-field px-3 text-sm text-text"><option value="">All services</option>{tags.map((value) => <option key={value}>{value}</option>)}</select>
          <Button variant="secondary" icon={<LuTrash2 className="size-4" />} onClick={live.clear}>Clear live view</Button>
        </div>
      </Card>
      {error && <p className="rounded-control bg-danger-soft p-3 text-sm text-danger">Could not load logs: {error}</p>}
      <EventFeedList events={events} emptyTitle="No matching logs" emptyDescription="Run a preview or synchronization; its structured events will appear here." ariaLabel="Application diagnostics" />
      <p className="text-xs text-text-3">The server retains the latest 5,000 entries. “Clear live view” only clears this browser view; it never destroys diagnostic history.</p>
    </div>
  )
}
