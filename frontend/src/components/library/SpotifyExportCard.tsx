import { useRef, useState } from 'react'
import { LuHistory, LuUpload } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import type { SpotifyExportImport } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

export function SpotifyExportCard({ onImported }: { onImported: () => void | Promise<void> }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [label, setLabel] = useState('My Spotify')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SpotifyExportImport | null>(null)

  async function upload() {
    if (!file || !label.trim()) return
    setBusy(true)
    setError(null)
    try {
      setResult(await api.restoreSpotifyExport(file, label.trim()))
      await onImported()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-control bg-success-soft text-success"><LuHistory className="size-5" /></span>
        <div>
          <h2 className="text-sm font-bold text-text">Import Spotify official data</h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-text-3">
            Upload the ZIP from Spotify's “Download your data”, or an individual JSON. Playlists, Your Library and
            streaming history enter the canonical database and monthly/yearly recap. Reimporting ignores duplicate plays.
          </p>
        </div>
      </div>
      <div className="grid gap-3 rounded-control border border-border bg-inset p-3 md:grid-cols-[minmax(12rem,1fr)_auto_auto] md:items-end">
        <label className="flex flex-col gap-1.5 text-xs font-medium text-text-2">
          Account label
          <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="My Spotify"
            className="h-10 rounded-control border border-border-strong bg-field px-3 text-sm text-text" />
        </label>
        <input ref={inputRef} type="file" accept=".zip,.json,application/zip,application/json" className="hidden"
          onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); setError(null) }} />
        <Button variant="secondary" icon={<LuUpload className="size-4" />} onClick={() => inputRef.current?.click()}>
          {file?.name ?? 'Choose ZIP/JSON'}
        </Button>
        <Button loading={busy} disabled={!file || !label.trim()} onClick={() => void upload()}>Import account</Button>
      </div>
      <p className="text-[11px] leading-relaxed text-text-3">
        The label creates a separate Spotify account slot. Use the same label on future exports to update that account;
        use another label for a second account. Streaming timestamps and durations form stable event IDs.
      </p>
      {error && <p role="alert" className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
      {result && <p role="status" className="rounded-control bg-success-soft px-3 py-2 text-sm text-success">
        Imported into {result.label}: {result.playlists.toLocaleString()} playlists, {result.liked_tracks.toLocaleString()} liked tracks and{' '}
        {result.listens_inserted.toLocaleString()} new listens ({result.listens_duplicates.toLocaleString()} duplicates ignored).
      </p>}
    </Card>
  )
}
