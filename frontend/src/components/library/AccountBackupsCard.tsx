import { useEffect, useRef, useState } from 'react'
import { LuArchiveRestore, LuDownload, LuUpload } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import type { LibraryAccount } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

export function AccountBackupsCard({ onRestored }: { onRestored: () => void | Promise<void> }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [accounts, setAccounts] = useState<LibraryAccount[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function refresh() {
    try { setAccounts(await api.getLibraryAccounts()) } catch (err) { setMessage(errorMessage(err)) }
  }
  useEffect(() => { void refresh() }, [])

  async function restore(file: File) {
    setBusy(true)
    setMessage(null)
    try {
      const result = await api.restoreAccountBackup(file)
      setMessage(`Restored ${result.label}: ${result.tracks} tracks, ${result.playlists} playlists and ${result.listens} listens.`)
      await refresh()
      await onRestored()
    } catch (err) {
      setMessage(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return <Card className="flex flex-col gap-4 p-4 sm:p-5">
    <div className="flex flex-wrap items-start gap-3">
      <span className="grid size-10 place-items-center rounded-control bg-info-soft text-info"><LuArchiveRestore className="size-5" /></span>
      <div className="min-w-0 flex-1"><h2 className="text-sm font-bold text-text">Backups by provider account</h2>
        <p className="mt-1 text-xs leading-relaxed text-text-3">Export or restore one account independently. These files contain its canonical playlists, surfaces and recap history, but never login cookies or provider credentials.</p></div>
      <input ref={inputRef} type="file" accept=".zip,.json,application/zip,application/json" className="hidden"
        onChange={(event) => { const file = event.target.files?.[0]; if (file) void restore(file); event.target.value = '' }} />
      <Button variant="secondary" loading={busy} icon={<LuUpload className="size-4" />} onClick={() => inputRef.current?.click()}>Restore account</Button>
    </div>
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {accounts.map((account) => <div key={account.id} className="flex items-center gap-2 rounded-control border border-border bg-inset px-3 py-2">
        <div className="min-w-0 flex-1"><strong className="block truncate text-xs text-text">{account.label}</strong><span className="font-mono text-[10px] text-text-3">{account.id}</span></div>
        <a href={`/api/library/accounts/${encodeURIComponent(account.id)}/backup`} title={`Export ${account.label}`}
          className="grid size-9 shrink-0 place-items-center rounded-control border border-border bg-surface-2 text-text-2 hover:text-text"><LuDownload className="size-4" /></a>
      </div>)}
    </div>
    {message && <p role="status" className="rounded-control bg-neutral-soft px-3 py-2 text-xs text-text-2">{message}</p>}
  </Card>
}
