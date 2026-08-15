import { useRef, useState } from 'react'
import { LuDatabaseBackup, LuUpload } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import type { MusifyBackupImport } from '@/types'

export function MusifyBackupCard({ onImported }: { onImported: () => void | Promise<void> }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<MusifyBackupImport | null>(null)

  async function upload() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const next = await api.restoreMusifyBackup(file)
      setResult(next)
      await onImported()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid size-10 place-items-center rounded-control bg-accent-soft text-accent"><LuDatabaseBackup className="size-5" /></span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-bold text-text">Import a Musify backup</h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-text-3">
            Upload <strong className="text-text-2">user.hive</strong> from Musify. Liked songs, recent plays,
            custom playlists, folders and listening recaps become a canonical snapshot. Imported playlists can
            then be copied from Musify to another service on the Transfers page.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-control border border-border bg-inset p-3">
        <input
          ref={inputRef}
          type="file"
          accept=".hive,.zip,application/zip"
          className="hidden"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null)
            setResult(null)
            setError(null)
          }}
        />
        <Button variant="secondary" icon={<LuUpload className="size-4" />} onClick={() => inputRef.current?.click()}>
          Choose user.hive
        </Button>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-3">
          {file?.name ?? 'No backup selected'}
        </span>
        <Button loading={busy} disabled={!file} onClick={() => void upload()}>Import into SYNC</Button>
      </div>

      <p className="text-[11px] leading-relaxed text-text-3">
        You may also upload a ZIP containing one user.hive. settings.hive is intentionally ignored because it
        contains preferences, not portable music data. Reimporting replaces Musify surfaces and monthly recap
        totals; it does not add the same month twice. Previous playlist contents are versioned first.
      </p>
      {error && <p role="alert" className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
      {result && (
        <p role="status" className="rounded-control bg-success-soft px-3 py-2 text-sm text-success">
          Imported {result.likedSongs.toLocaleString()} liked songs, {result.playlists.toLocaleString()} playlists,
          {' '}{result.playlistTracks.toLocaleString()} playlist tracks and {result.listeningStats.toLocaleString()} recap entries.
        </p>
      )}
    </Card>
  )
}
