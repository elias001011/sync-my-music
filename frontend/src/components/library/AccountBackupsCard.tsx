import { useEffect, useRef, useState } from 'react'
import { LuArchiveRestore, LuCheck, LuDownload, LuPencil, LuTrash2, LuUpload, LuX } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import type { LibraryAccount } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Modal } from '../ui/Modal'

function fmtTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

/** Human label for the import kind that created the slot (auth_mode). */
function importKind(account: LibraryAccount): string {
  const mode = account.auth_mode || ''
  if (mode.includes('official-export')) return 'Official export'
  if (mode.includes('sync-account-restore')) return 'Restored backup'
  if (mode.includes('history-import')) return 'Listening import'
  if (mode.includes('aggregate-import')) return 'Recap snapshot'
  if (mode.includes('cookie') || mode.includes('oauth')) return 'Live connection'
  return account.status === 'connected' ? 'Live connection' : 'Local slot'
}

function AccountRow({ account, onChanged, onError }: {
  account: LibraryAccount
  onChanged: () => void | Promise<void>
  onError: (message: string) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState(account.label)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  async function rename() {
    const next = label.trim()
    if (!next || next === account.label) {
      setEditing(false)
      setLabel(account.label)
      return
    }
    setBusy(true)
    try {
      await api.renameLibraryAccount(account.id, next)
      setEditing(false)
      await onChanged()
    } catch (err) {
      onError(errorMessage(err))
      setLabel(account.label)
      setEditing(false)
    } finally {
      setBusy(false)
    }
  }

  async function replace(file: File) {
    setBusy(true)
    try {
      const result = await api.restoreAccountBackup(file, account.id)
      onError(`Replaced ${result.label}: ${result.tracks} tracks, ${result.playlists} playlists, ${result.listens} listens.`)
      await onChanged()
    } catch (err) {
      onError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await api.deleteLibraryAccount(account.id)
      setConfirming(false)
      await onChanged()
    } catch (err) {
      onError(errorMessage(err))
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-control border border-border bg-inset p-3">
      <div className="flex items-start gap-2">
        {editing ? (
          <input value={label} onChange={(event) => setLabel(event.target.value)} autoFocus
            onKeyDown={(event) => { if (event.key === 'Enter') void rename(); if (event.key === 'Escape') { setEditing(false); setLabel(account.label) } }}
            className="h-8 w-full min-w-0 flex-1 rounded-control border border-border bg-field px-2 text-xs text-text" />
        ) : (
          <strong className="min-w-0 flex-1 truncate text-xs text-text">{account.label}</strong>
        )}
        <div className="flex shrink-0 items-center gap-1">
          {editing ? (
            <>
              <button type="button" onClick={() => void rename()} disabled={busy} title="Save name"
                className="grid size-7 place-items-center rounded-control bg-accent-soft text-accent hover:text-accent"><LuCheck className="size-3.5" /></button>
              <button type="button" onClick={() => { setEditing(false); setLabel(account.label) }} title="Cancel"
                className="grid size-7 place-items-center rounded-control text-text-3 hover:bg-surface-2"><LuX className="size-3.5" /></button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => setEditing(true)} title="Rename slot"
                className="grid size-7 place-items-center rounded-control text-text-3 hover:bg-surface-2 hover:text-text"><LuPencil className="size-3.5" /></button>
              <a href={`/api/library/accounts/${encodeURIComponent(account.id)}/backup`} title="Export backup"
                className="grid size-7 place-items-center rounded-control text-text-3 hover:bg-surface-2 hover:text-text"><LuDownload className="size-3.5" /></a>
              <button type="button" onClick={() => inputRef.current?.click()} title="Replace from backup"
                className="grid size-7 place-items-center rounded-control text-text-3 hover:bg-surface-2 hover:text-text"><LuUpload className="size-3.5" /></button>
              <button type="button" onClick={() => setConfirming(true)} title="Remove account slot"
                className="grid size-7 place-items-center rounded-control text-danger hover:bg-danger-soft"><LuTrash2 className="size-3.5" /></button>
              <input ref={inputRef} type="file" accept=".zip,.json,application/zip,application/json" className="hidden"
                onChange={(event) => { const file = event.target.files?.[0]; if (file) void replace(file); event.target.value = '' }} />
            </>
          )}
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-text-3">
        <span>{account.id}</span>
        <span className="capitalize">{account.provider}</span>
        <span>{importKind(account)}</span>
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-text-2">
        <span>{(account.tracks ?? 0).toLocaleString()} tracks</span>
        <span>{(account.playlists ?? 0).toLocaleString()} playlists</span>
        <span>{(account.surfaces ?? 0).toLocaleString()} surface items</span>
        <span>{(account.listens ?? 0).toLocaleString()} listens</span>
      </div>
      <div className="mt-1.5 text-[10px] text-text-3">Last update: {fmtTime(account.updated_at)}</div>

      <Modal open={confirming} onClose={() => setConfirming(false)} title="Remove this account slot?"
        description={`This deletes ${account.label} (${account.id}) and the imported data that only it owns. Canonical tracks, playlists and artists still used by other accounts are kept. This cannot be undone — export a backup first if you may need it again.`}
        footer={<>
          <Button variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
          <Button variant="danger-ghost" loading={busy} icon={<LuTrash2 className="size-4" />} onClick={() => void remove()}>Remove account</Button>
        </>}>
        <p className="text-sm text-text-2">Type confirmation is not required — this dialog plus the button below is the explicit confirmation.</p>
      </Modal>
    </div>
  )
}

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
      <div className="min-w-0 flex-1"><h2 className="text-sm font-bold text-text">Account snapshots</h2>
        <p className="mt-1 text-xs leading-relaxed text-text-3">One slot per imported account — rename it, export its backup, replace it from a backup, or remove it. These files contain canonical playlists, surfaces and recap history, but never login cookies or provider credentials. Removing a slot keeps canonical entities still used by other accounts.</p></div>
      <input ref={inputRef} type="file" accept=".zip,.json,application/zip,application/json" className="hidden"
        onChange={(event) => { const file = event.target.files?.[0]; if (file) void restore(file); event.target.value = '' }} />
      <Button variant="secondary" loading={busy} icon={<LuUpload className="size-4" />} onClick={() => inputRef.current?.click()}>Restore as new account</Button>
    </div>
    {accounts.length > 0 && (
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {accounts.map((account) => <AccountRow key={account.id} account={account} onChanged={refresh}
          onError={(next) => setMessage(next)} />)}
      </div>
    )}
    {message && <p role="status" className="rounded-control bg-neutral-soft px-3 py-2 text-xs text-text-2">{message}</p>}
  </Card>
}
