import { useEffect, useRef, useState } from 'react'
import { LuDownload, LuSheet, LuUpload } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import type { CsvImportResult, LibraryCollection } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

export function CsvLibraryCard({ onImported }: { onImported: () => void | Promise<void> }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [label, setLabel] = useState('CSV import')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<CsvImportResult | null>(null)

  const [collections, setCollections] = useState<LibraryCollection[]>([])
  const [selected, setSelected] = useState('')

  useEffect(() => {
    void refreshCollections()
  }, [])

  async function refreshCollections() {
    try {
      const rows = await api.getLibraryCollections()
      setCollections(rows)
      setSelected((current) => current || rows[0]?.id || '')
    } catch {
      // The picker is a convenience — a failed refresh just leaves it empty.
    }
  }

  async function upload() {
    if (!file || !name.trim()) return
    setBusy(true)
    setError(null)
    try {
      setResult(await api.importCsvPlaylist(file, name.trim(), label.trim() || 'CSV import'))
      await onImported()
      await refreshCollections()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-control bg-accent-soft text-accent"><LuSheet className="size-5" /></span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-bold text-text">CSV playlist import / export</h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-text-3">
            Spotify has no native CSV export — a tool like Exportify reads your account and writes a spreadsheet.
            Import that (or any CSV with Title/Artist/Album-style columns) as a canonical playlist, or export one of
            your canonical playlists back out to a CSV file.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-control border border-border bg-inset p-3">
        <label className="flex min-w-40 flex-1 flex-col gap-1.5 text-xs font-medium text-text-2">
          Playlist name
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="My Playlist"
            className="h-10 rounded-control border border-border-strong bg-field px-3 text-sm text-text" />
        </label>
        <label className="flex min-w-40 flex-1 flex-col gap-1.5 text-xs font-medium text-text-2">
          Account label
          <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="CSV import"
            className="h-10 rounded-control border border-border-strong bg-field px-3 text-sm text-text" />
        </label>
        <input ref={inputRef} type="file" accept=".csv,text/csv" className="hidden"
          onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); setError(null) }} />
        <Button variant="secondary" icon={<LuUpload className="size-4" />} onClick={() => inputRef.current?.click()}>
          {file?.name ?? 'Choose CSV'}
        </Button>
        <Button loading={busy} disabled={!file || !name.trim()} onClick={() => void upload()}>Import playlist</Button>
      </div>

      {error && <p role="alert" className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
      {result && (
        <p role="status" className="rounded-control bg-success-soft px-3 py-2 text-sm text-success">
          Imported {result.tracks.toLocaleString()} tracks into {result.label}.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3 rounded-control border border-border bg-inset p-3">
        <label className="flex min-w-48 flex-1 flex-col gap-1.5 text-xs font-medium text-text-2">
          Export a canonical playlist
          <select value={selected} onChange={(event) => setSelected(event.target.value)}
            disabled={collections.length === 0}
            className="h-10 rounded-control border border-border-strong bg-field px-3 text-sm text-text disabled:opacity-50">
            {collections.length === 0
              ? <option value="">No playlists yet</option>
              : collections.map((c) => (
                <option key={c.id} value={c.id}>{c.title} ({c.track_count.toLocaleString()} tracks)</option>
              ))}
          </select>
        </label>
        <Button
          variant="secondary"
          icon={<LuDownload className="size-4" />}
          disabled={!selected}
          onClick={() => { if (selected) window.location.href = api.csvExportUrl(selected) }}
        >
          Download CSV
        </Button>
      </div>
    </Card>
  )
}
