import { useEffect, useMemo, useState } from 'react'
import { LuClock3, LuHeadphones, LuMusic2, LuUsers, LuX } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { cn } from '@/lib/cn'
import type { LibraryAccount, Recap, RecapHistory } from '@/types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

/** One toggle chip in the account filter row; empty selection = unified recap. */
function AccountChip({ account, selected, onToggle }: { account: LibraryAccount; selected: boolean; onToggle: () => void }) {
  return (
    <button type="button" onClick={onToggle}
      className={cn('rounded-full border px-3 py-1.5 text-xs transition',
        selected ? 'border-accent bg-accent-soft text-accent' : 'border-border bg-surface-2 text-text-2 hover:border-accent/60 hover:text-text')}>
      {account.label}
    </button>
  )
}

function Stats({ recap }: { recap: Recap | null }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {[
        { label: 'Plays', value: recap?.plays ?? 0, icon: LuHeadphones },
        { label: 'Minutes', value: Math.round((recap?.listened_ms ?? 0) / 60000), icon: LuClock3 },
        { label: 'Unique tracks', value: recap?.tracks ?? 0, icon: LuMusic2 },
        { label: 'Artists', value: recap?.artists ?? 0, icon: LuUsers },
      ].map((stat) => (
        <Card key={stat.label} className="p-4">
          <stat.icon className="mb-4 size-4 text-accent" />
          <strong className="block text-2xl text-text">{stat.value.toLocaleString()}</strong>
          <span className="text-xs text-text-3">{stat.label}</span>
        </Card>
      ))}
    </div>
  )
}

export default function Recaps() {
  const currentDate = new Date()
  const [year, setYear] = useState(currentDate.getFullYear())
  const [recap, setRecap] = useState<Recap | null>(null)
  const [history, setHistory] = useState<RecapHistory | null>(null)
  const [selectedMonth, setSelectedMonth] = useState<number | null>(null)
  const [monthRecap, setMonthRecap] = useState<Recap | null>(null)
  const [accounts, setAccounts] = useState<LibraryAccount[]>([])
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  // Accounts available for the filter; empty selection = unified recap.
  useEffect(() => {
    let active = true
    api.getLibraryAccounts()
      .then((rows) => { if (active) setAccounts(rows) })
      .catch(() => { /* the recap itself stays usable without the filter */ })
    return () => { active = false }
  }, [])

  function toggleAccount(accountId: string) {
    setSelectedAccounts((current) =>
      current.includes(accountId) ? current.filter((id) => id !== accountId) : [...current, accountId])
  }

  useEffect(() => {
    let active = true
    api.getRecapHistory(selectedAccounts)
      .then((next) => { if (active) setHistory(next) })
      .catch((err: unknown) => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [selectedAccounts])

  useEffect(() => {
    setError(null)
    setRecap(null)
    setSelectedMonth(null)
    setMonthRecap(null)
    let active = true
    api.getRecap(year, undefined, selectedAccounts)
      .then((next) => { if (active) setRecap(next) })
      .catch((err: unknown) => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [year, selectedAccounts])

  useEffect(() => {
    if (!selectedMonth) return
    setMonthRecap(null)
    let active = true
    api.getRecap(year, selectedMonth, selectedAccounts)
      .then((next) => { if (active) setMonthRecap(next) })
      .catch((err: unknown) => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [selectedMonth, year, selectedAccounts])

  const annualMonths = useMemo(
    () => MONTHS.map((label, index) => ({
      label,
      plays: recap?.by_month.find((item) => item.month === index + 1)?.plays ?? 0,
    })),
    [recap],
  )
  const max = Math.max(1, ...annualMonths.map((item) => item.plays))
  const years = history
    ? Array.from({ length: history.retention_years }, (_, index) => history.current_year - index)
    : [year]
  const visibleMonthCount = year === currentDate.getFullYear() ? currentDate.getMonth() + 1 : 12
  const monthSummaries = Array.from({ length: visibleMonthCount }, (_, index) => {
    const month = index + 1
    return history?.months.find((item) => item.year === year && item.month === month) ?? {
      year, month, plays: 0, tracks: 0, artists: 0, listened_ms: 0,
    }
  }).reverse()

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="font-mono text-[10px] font-bold tracking-[0.14em] text-accent">ALL SERVICES · ONE STORY</span>
          <h1 className="mt-1 text-xl font-bold tracking-tight text-text sm:text-[22px]">Listening recap</h1>
          <p className="mt-1 text-sm text-text-3">Annual highlights with a month-by-month history across every imported service.</p>
        </div>
        <select value={year} onChange={(event) => setYear(Number(event.target.value))} className="h-10 rounded-control border border-border bg-field px-3 text-sm text-text">
          {years.map((item) => <option key={item}>{item}</option>)}
        </select>
      </div>

      {error && <p className="rounded-control bg-danger-soft p-3 text-sm text-danger">{error}</p>}

      {accounts.length > 0 && (
        <Card className="flex flex-wrap items-center gap-2 p-3">
          <span className="font-mono text-[10px] font-bold tracking-[0.12em] text-text-3">FILTER BY ACCOUNT</span>
          <button type="button" onClick={() => setSelectedAccounts([])}
            className={cn('rounded-full border px-3 py-1.5 text-xs transition',
              selectedAccounts.length === 0 ? 'border-accent bg-accent-soft text-accent' : 'border-border bg-surface-2 text-text-2 hover:text-text')}>
            All accounts
          </button>
          {accounts.map((account) => (
            <AccountChip key={account.id} account={account} selected={selectedAccounts.includes(account.id)}
              onToggle={() => toggleAccount(account.id)} />
          ))}
          {selectedAccounts.length > 0 && (
            <span className="ml-auto text-xs text-text-3">Totals below only include the {selectedAccounts.length} selected account{selectedAccounts.length > 1 ? 's' : ''}.</span>
          )}
        </Card>
      )}

      <div>
        <span className="font-mono text-[10px] font-bold tracking-[0.14em] text-text-3">{year} OVERVIEW</span>
        <div className="mt-3"><Stats recap={recap} /></div>
      </div>

      <Card className="p-4 sm:p-5">
        <h2 className="text-sm font-bold text-text">Listening rhythm</h2>
        <div className="mt-6 flex h-44 items-end gap-2">
          {annualMonths.map((item) => (
            <div key={item.label} className="flex h-full flex-1 flex-col items-center justify-end gap-2">
              <span className="font-mono text-[9px] text-text-3">{item.plays || ''}</span>
              <div className="w-full max-w-10 rounded-t-chip bg-accent" style={{ height: `${Math.max(item.plays ? 8 : 2, item.plays / max * 100)}%` }} />
              <span className="font-mono text-[9px] text-text-3">{item.label}</span>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4 sm:p-5">
          <h2 className="mb-3 text-sm font-bold text-text">Top tracks</h2>
          {recap?.top_tracks.length ? (
            <ol className="divide-y divide-border">{recap.top_tracks.map((track, index) => (
              <li key={track.id} className="flex items-center gap-3 py-3">
                <span className="w-5 font-mono text-xs text-text-3">{index + 1}</span>
                <div className="min-w-0 flex-1"><strong className="block truncate text-sm text-text">{track.title}</strong><span className="text-xs text-text-3">{track.artist}</span></div>
                <span className="font-mono text-xs text-text-2">{track.plays} plays</span>
              </li>
            ))}</ol>
          ) : <p className="py-10 text-center text-sm text-text-3">No listening data for {year} yet.</p>}
        </Card>
        <Card className="p-4 sm:p-5">
          <h2 className="mb-3 text-sm font-bold text-text">Services</h2>
          {recap?.services.length ? <div className="flex flex-col gap-3">{recap.services.map((service) => (
            <div key={`${service.account_id ?? 'none'}-${service.source}`}>
              <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                <span className="min-w-0"><span className="font-semibold capitalize text-text-2">{service.account_label ?? service.source}</span><span className="ml-2 font-mono text-[10px] text-text-3">{service.source}</span></span>
                <span className="font-mono text-text-3">{service.plays} plays</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-inset"><div className="h-full rounded-full bg-accent" style={{ width: `${service.plays / Math.max(1, recap.plays) * 100}%` }} /></div>
            </div>
          ))}</div> : <p className="py-10 text-center text-sm text-text-3">Sources appear here as listening events arrive.</p>}
        </Card>
      </div>

      <section>
        <div className="mb-3">
          <h2 className="text-base font-bold text-text">Monthly history</h2>
          <p className="mt-0.5 text-xs text-text-3">Select a month to open its complete recap. Retaining {history?.retention_years ?? 3} years.</p>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {monthSummaries.map((month) => (
            <button key={`${month.year}-${month.month}`} type="button" onClick={() => setSelectedMonth(month.month)} className={cn('rounded-card border bg-surface p-4 text-left shadow-sm transition hover:border-accent', selectedMonth === month.month ? 'border-accent ring-1 ring-accent' : 'border-border')}>
              <span className="font-mono text-[10px] font-bold tracking-[0.12em] text-accent">{MONTH_NAMES[month.month - 1].toUpperCase()}</span>
              <strong className="mt-2 block text-xl text-text">{month.plays.toLocaleString()}</strong>
              <span className="text-xs text-text-3">plays · {Math.round(month.listened_ms / 60000).toLocaleString()} min</span>
              <span className="mt-2 block text-[11px] text-text-3">{month.tracks} tracks · {month.artists} artists</span>
            </button>
          ))}
        </div>
      </section>

      {selectedMonth && (
        <section className="flex flex-col gap-4 rounded-card border border-accent/40 bg-inset p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3">
            <div><span className="font-mono text-[10px] font-bold tracking-[0.14em] text-accent">MONTHLY RECAP</span><h2 className="mt-1 text-lg font-bold text-text">{MONTH_NAMES[selectedMonth - 1]} {year}</h2></div>
            <Button variant="ghost" size="sm" icon={<LuX className="size-4" />} onClick={() => { setSelectedMonth(null); setMonthRecap(null) }}>Close</Button>
          </div>
          <Stats recap={monthRecap} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="p-4"><h3 className="mb-2 text-sm font-bold text-text">Top tracks this month</h3>{monthRecap?.top_tracks.length ? <ol className="divide-y divide-border">{monthRecap.top_tracks.slice(0, 5).map((track, index) => <li key={track.id} className="flex gap-3 py-2 text-xs"><span className="font-mono text-text-3">{index + 1}</span><span className="min-w-0 flex-1 truncate text-text-2">{track.title} · {track.artist}</span><span className="font-mono text-text-3">{track.plays}</span></li>)}</ol> : <p className="py-5 text-center text-xs text-text-3">No listens imported for this month.</p>}</Card>
            <Card className="p-4"><h3 className="mb-2 text-sm font-bold text-text">Services this month</h3>{monthRecap?.services.length ? <div className="flex flex-col gap-2">{monthRecap.services.map((service) => <div key={`${service.account_id ?? 'none'}-${service.source}`} className="flex justify-between gap-2 text-xs"><span className="min-w-0"><span className="font-semibold capitalize text-text-2">{service.account_label ?? service.source}</span><span className="ml-2 font-mono text-[10px] text-text-3">{service.source}</span></span><span className="font-mono text-text-3">{service.plays} plays · {Math.round(service.listened_ms / 60000)} min</span></div>)}</div> : <p className="py-5 text-center text-xs text-text-3">No service data for this month.</p>}</Card>
          </div>
        </section>
      )}
    </div>
  )
}
