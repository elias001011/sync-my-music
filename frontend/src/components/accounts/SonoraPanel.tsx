import { useEffect, useState } from 'react'
import { LuDownload, LuRadio, LuRefreshCw, LuUpload, LuWifi } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import type { SonoraDevice, SonoraStatus } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

const SURFACES = [
  ['likedSongs', 'Liked songs'], ['followedArtists', 'Artists'], ['likedAlbums', 'Albums'],
  ['likedPlaylists', 'Saved playlists'], ['playlists', 'Local playlists'], ['history', 'Listening recap'],
] as const

export function SonoraPanel() {
  const [status, setStatus] = useState<SonoraStatus | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pins, setPins] = useState<Record<string, string>>({})
  const [surfaces, setSurfaces] = useState<string[]>(SURFACES.map(([id]) => id))

  function refresh() { api.getSonoraStatus().then(setStatus).catch((err: unknown) => setError(errorMessage(err))) }
  useEffect(refresh, [])
  async function action(name: string, fn: () => Promise<unknown>) {
    setBusy(name); setError(null)
    try { await fn(); refresh() } catch (err) { setError(errorMessage(err)) } finally { setBusy('') }
  }
  function toggleSurface(id: string) { setSurfaces((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]) }
  function deviceKey(device: SonoraDevice) { return `${device.ip}:${device.port}` }

  return (
    <Card className="flex flex-col gap-5 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-3"><span className="grid size-11 place-items-center rounded-card bg-info-soft text-info"><LuRadio className="size-5" /></span><div className="min-w-0 flex-1"><h2 className="font-bold text-text">Sonora bridge</h2><p className="mt-1 max-w-3xl text-[13px] leading-relaxed text-text-3">Import/export the complete Sonora backup or let this server appear as a paired Sonora device on your Wi-Fi. LAN discovery is off by default.</p></div><Button variant={status?.enabled ? 'secondary' : 'primary'} loading={busy === 'toggle'} onClick={() => void action('toggle', () => api.setSonoraEnabled(!status?.enabled))}>{status?.enabled ? 'Disable LAN bridge' : 'Enable LAN bridge'}</Button></div>
      {error && <p className="rounded-control bg-danger-soft p-3 text-sm text-danger">{error}</p>}
      {status?.pending.map((pending) => <div key={pending.client_id} className="rounded-control border border-warning bg-warning-soft p-3"><strong className="text-sm text-text">{pending.client_name} wants to pair</strong><p className="mt-1 text-xs text-text-2">Enter this PIN in Sonora: <span className="ml-1 font-mono text-lg font-bold text-warning">{pending.pin}</span></p></div>)}
      <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
        <div><span className="mb-2 block font-mono text-[10px] font-bold tracking-wider text-text-3">SURFACES INCLUDED</span><div className="flex flex-wrap gap-2">{SURFACES.map(([id, label]) => <label key={id} className={`cursor-pointer rounded-chip border px-2.5 py-1.5 text-xs ${surfaces.includes(id) ? 'border-accent bg-accent-soft text-accent' : 'border-border text-text-3'}`}><input type="checkbox" checked={surfaces.includes(id)} onChange={() => toggleSurface(id)} className="sr-only" />{label}</label>)}</div></div>
        <div className="flex flex-wrap items-end gap-2"><a href="/api/sonora/backup" className="inline-flex h-10 items-center gap-2 rounded-control border border-border bg-surface-2 px-3 text-sm font-semibold text-text"><LuDownload className="size-4" />Export backup</a><label className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-control border border-border bg-surface-2 px-3 text-sm font-semibold text-text"><LuUpload className="size-4" />Import backup<input type="file" accept=".zip,application/zip" className="sr-only" onChange={(event) => { const file = event.target.files?.[0]; if (file) void action('upload', () => api.restoreSonoraBackup(file)) }} /></label></div>
      </div>
      {status?.enabled && <div className="border-t border-border pt-4"><div className="mb-3 flex items-center justify-between"><div><h3 className="text-sm font-bold text-text">Nearby and paired devices</h3><p className="text-xs text-text-3">Only paired devices can exchange library data.</p></div><Button variant="secondary" size="sm" icon={<LuWifi className="size-4" />} loading={busy === 'discover'} onClick={() => void action('discover', api.discoverSonora)}>Scan LAN</Button></div>{status.devices.length === 0 ? <p className="rounded-control bg-inset p-4 text-center text-xs text-text-3">No Sonora devices found yet.</p> : <div className="flex flex-col gap-2">{status.devices.map((device) => <div key={device.device_id} className="flex flex-wrap items-center gap-3 rounded-control border border-border p-3"><span className={`size-2 rounded-full ${device.paired ? 'bg-success' : 'bg-warning'}`} /><div className="min-w-44 flex-1"><strong className="block text-sm text-text">{device.name}</strong><span className="font-mono text-[10px] text-text-3">{device.ip}:{device.port}</span></div>{device.paired ? <Button size="sm" icon={<LuRefreshCw className="size-4" />} loading={busy === device.device_id} onClick={() => void action(device.device_id, () => api.syncSonora(device.device_id, surfaces))}>Sync selected</Button> : <><input value={pins[deviceKey(device)] ?? ''} onChange={(e) => setPins({ ...pins, [deviceKey(device)]: e.target.value })} placeholder="PIN shown in Sonora" className="h-9 w-40 rounded-control border border-border bg-field px-3 font-mono text-xs text-text" />{pins[deviceKey(device)] ? <Button size="sm" variant="secondary" onClick={() => void action(device.device_id, () => api.verifySonoraPair(device.ip, device.port, pins[deviceKey(device)]))}>Verify PIN</Button> : <Button size="sm" variant="secondary" onClick={() => void action(device.device_id, () => api.requestSonoraPair(device.ip, device.port))}>Start pairing</Button>}</>}</div>)}</div>}</div>}
    </Card>
  )
}
