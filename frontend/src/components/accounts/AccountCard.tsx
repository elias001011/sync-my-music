import { useState } from 'react'

import { api, errorMessage } from '@/api'
import { cn } from '@/lib/cn'
import { serviceLogoId, tagDot, tagText } from '@/lib/constants'
import type { Account, AuthKind, SurfaceName } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { ConfirmDialog } from '../ui/ConfirmDialog'
import { Modal } from '../ui/Modal'
import { ServiceLogo } from '../ui/ServiceLogo'
import { StatusPill } from '../ui/StatusPill'
import { TextField } from '../ui/TextField'
import { Toggle } from '../ui/Toggle'
import { ConnectWizardModal } from './ConnectWizardModal'

const SURFACE_LABELS: Record<SurfaceName, { label: string; hint: string }> = {
  playlists: { label: 'Playlists', hint: 'Sync and transfer playlist content.' },
  liked_tracks: { label: 'Liked tracks', hint: 'Import and sync saved songs.' },
  saved_albums: { label: 'Saved albums', hint: 'Import albums saved to the library.' },
  followed_artists: { label: 'Followed artists', hint: 'Import artists you follow.' },
  history: { label: 'Listening history', hint: 'Import scrobbles and recap snapshots (never writes back).' },
}

const SURFACE_ORDER: SurfaceName[] = ['playlists', 'liked_tracks', 'saved_albums', 'followed_artists', 'history']

const SURFACE_CAP_LABELS: Record<string, string> = {
  rw: 'READ + WRITE',
  r: 'READ / IMPORT',
  '-': 'NOT SUPPORTED',
}

const SERVICE_BLURBS: Record<string, string> = {
  spotify: 'Syncs playlists through Spotify OAuth as either a source or destination.',
  tidal: 'Syncs playlists using the OpenAPI session from your signed-in TIDAL web player.',
  qobuz: 'Syncs playlists using the minimized API context from your signed-in Qobuz web player.',
  deezer: 'Syncs playlists using an auto-renewing session from your signed-in Deezer web player.',
  amazon: 'Syncs playlists using an auto-renewing session from your signed-in Amazon Music web player.',
  apple: 'Paste a couple of tokens from the Apple Music web player. No developer account needed.',
  ytmusic: 'Sign in with a Google account using a short code. Approve it from your phone or another tab.',
  jellyfin: 'Optional. Pushes real playlist cover art to your Jellyfin server.',
  musify: 'Read-only snapshot imported from user.hive. Reimport it from the Library page whenever Musify changes.',
}

const AUTH_KIND_LABELS: Record<AuthKind, string> = {
  oauth_redirect: 'OAUTH',
  oauth_device: 'DEVICE CODE',
  token_paste: 'TOKEN PASTE',
  api_key: 'API KEY',
}

/** Card border echoes severity: hairline for healthy, dashed for "nothing
 * here yet", solid danger only for errors. */
function borderClass(state: Account['state']): string {
  if (state === 'error') return 'border-danger'
  if (state === 'unconfigured') return 'border-dashed border-border-strong'
  return 'border-border'
}

export function AccountCard({ account, onChanged }: { account: Account; onChanged: () => void }) {
  const [wizardOpen, setWizardOpen] = useState(false)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [confirmingRemove, setConfirmingRemove] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState(account.name)
  const [renaming, setRenaming] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [addValue, setAddValue] = useState('')
  const [adding, setAdding] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const isConnected = account.state === 'connected' || account.state === 'expired'
  const visualProvider = account.provider ?? account.id
  const logoId = serviceLogoId(visualProvider)
  // The stable account id: `spotify:default` or `spotify:work` (named).
  const accountId = account.account_id ?? (account.id.includes(':') ? account.id : `${account.id}:default`)
  // `:default` profiles are the migration anchor — they can be disconnected but
  // never removed; adding another account happens from the default's card.
  const isDefault = accountId.endsWith(':default')
  const isLive = account.live !== false && !account.local_snapshot

  async function disconnect() {
    setDisconnecting(true)
    setError(null)
    try {
      await api.disconnectAccount(account.id)
      setConfirmingDisconnect(false)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setDisconnecting(false)
    }
  }

  async function remove() {
    setRemoving(true)
    setError(null)
    try {
      await api.removeAccount(account.id)
      setConfirmingRemove(false)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRemoving(false)
    }
  }

  async function rename() {
    setRenaming(true)
    setError(null)
    try {
      await api.setAccountPrefs(account.id, { label: renameValue.trim() })
      setRenameOpen(false)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRenaming(false)
    }
  }

  async function toggleEnabled() {
    setError(null)
    try {
      await api.setAccountEnabled(account.id, account.enabled === false)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const surfaceCaps = account.surface_capabilities
  const surfaces = account.surfaces ?? {}

  async function toggleSurface(surface: SurfaceName, enabled: boolean) {
    setError(null)
    try {
      await api.setAccountPrefs(account.id, { surfaces: { [surface]: enabled } })
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function addAccount() {
    setAdding(true)
    setError(null)
    try {
      await api.createAccount(visualProvider, addValue.trim())
      setAddOpen(false)
      setAddValue('')
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setAdding(false)
    }
  }

  async function importToLibrary() {
    setImporting(true)
    setError(null)
    setImportResult(null)
    try {
      const res = await api.importLiveAccount(accountId)
      setImportResult(`Imported ${res.playlists} playlists · ${res.playlist_tracks} tracks.`)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setImporting(false)
    }
  }

  return (
    <Card className={cn('flex flex-col gap-3.5 p-4 sm:p-5', borderClass(account.state))}>
      <div className="flex flex-wrap items-center gap-2.5">
        {logoId ? (
          <span
            className="grid size-11 shrink-0 place-items-center overflow-hidden rounded-card border border-border bg-surface-2"
            aria-hidden="true"
          >
            <ServiceLogo service={logoId} className={cn('size-6', tagText(visualProvider))} />
          </span>
        ) : (
          <span className={cn('size-2.5 shrink-0 rounded-full', tagDot(visualProvider))} aria-hidden="true" />
        )}
        <div className="min-w-0">
          <h3 className="text-base font-bold text-text">{account.name}</h3>
          {isLive && (
            <span className="font-mono text-[10px] tracking-wide text-text-3">{accountId}</span>
          )}
        </div>
        <span className="font-mono text-[10px] tracking-wide text-text-3">
          {account.local_snapshot ? 'LOCAL SNAPSHOT' : isLive && !isDefault ? 'LIVE ACCOUNT' : AUTH_KIND_LABELS[account.auth_kind]}
        </span>
        <StatusPill state={account.state} className="ml-auto" />
      </div>

      <p className="text-[13px] leading-relaxed text-text-2">{SERVICE_BLURBS[visualProvider] ?? ''}</p>

      <div className="flex flex-wrap gap-1.5">
        {account.capabilities?.library_read && <span className="rounded-chip bg-info-soft px-2 py-1 font-mono text-[9px] text-info">LIBRARY READ</span>}
        {account.capabilities?.playlist_read && <span className="rounded-chip bg-neutral-soft px-2 py-1 font-mono text-[9px] text-neutral">PLAYLIST READ</span>}
        {account.capabilities?.playlist_write && <span className="rounded-chip bg-success-soft px-2 py-1 font-mono text-[9px] text-success">PLAYLIST WRITE</span>}
        {account.enabled === false && <span className="rounded-chip bg-warning-soft px-2 py-1 font-mono text-[9px] text-warning">PAUSED</span>}
      </div>

      {account.detail && account.state !== 'connected' && account.state !== 'error' && (
        <p className="text-xs leading-relaxed text-text-3">{account.detail}</p>
      )}

      {account.state === 'error' && account.detail && (
        <div className="flex gap-2.5 rounded-control bg-danger-soft px-3.5 py-2.5">
          <span className="font-mono text-xs font-semibold text-danger" aria-hidden="true">
            !
          </span>
          <p className="text-[12.5px] leading-relaxed text-text-2">{account.detail}</p>
        </div>
      )}

      {importResult && <p className="text-xs text-success">{importResult}</p>}
      {error && <p className="text-xs text-danger">{error}</p>}

      {surfaceCaps && !account.local_snapshot && (
        <div className="space-y-1 border-t border-border pt-3">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <span className="font-mono text-[10px] font-bold tracking-[0.12em] text-text-3">SURFACES</span>
            <span className="text-[11px] text-text-3">Disabling a surface stops new imports; existing data stays.</span>
          </div>
          {SURFACE_ORDER.filter((surface) => surfaceCaps[surface] !== undefined).map((surface) => {
            const cap = surfaceCaps[surface] ?? '-'
            const meta = SURFACE_LABELS[surface]
            return (
              <div key={surface} className="flex items-center gap-3 rounded-control px-1.5 py-1">
                <div className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-sm font-medium text-text">
                    {meta.label}
                    <span className={cn('rounded-chip px-1.5 py-0.5 font-mono text-[8px]',
                      cap === 'rw' ? 'bg-success-soft text-success' : cap === 'r' ? 'bg-info-soft text-info' : 'bg-neutral-soft text-neutral')}>
                      {SURFACE_CAP_LABELS[cap]}
                    </span>
                  </span>
                  <span className="block text-[11px] text-text-3">{meta.hint}</span>
                </div>
                {cap !== '-' && (
                  <Toggle hideLabel label={meta.label} checked={surfaces[surface] ?? true}
                    onChange={(enabled) => void toggleSurface(surface, enabled)} />
                )}
              </div>
            )
          })}
        </div>
      )}

      {isLive && (
        <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Button variant={isConnected ? 'secondary' : 'primary'} size="sm" onClick={() => setWizardOpen(true)}>
            {isConnected ? 'Reconnect' : 'Connect'}
          </Button>
          {isConnected && (
            <Button variant="ghost" size="sm" onClick={() => setConfirmingDisconnect(true)}>
              Disconnect
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => void toggleEnabled()}>
            {account.enabled === false ? 'Enable' : 'Pause'}
          </Button>
          {!isDefault && (
            <>
              <Button variant="ghost" size="sm" onClick={() => { setRenameValue(account.name); setRenameOpen(true) }}>
                Rename
              </Button>
              <Button variant="ghost" size="sm" className="text-danger hover:bg-danger-soft/50" onClick={() => setConfirmingRemove(true)}>
                Remove
              </Button>
            </>
          )}
          {account.state === 'connected' && (
            <Button variant="ghost" size="sm" onClick={() => void importToLibrary()} loading={importing} className="ml-auto">
              Import playlists
            </Button>
          )}
          {isDefault && (
            <Button variant="ghost" size="sm" onClick={() => setAddOpen(true)}>
              + Add account
            </Button>
          )}
        </div>
      )}

      {!account.local_snapshot && <ConnectWizardModal
        account={account}
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onConnected={() => {
          setWizardOpen(false)
          onChanged()
        }}
        onChanged={onChanged}
      />}

      {!account.local_snapshot && <ConfirmDialog
        open={confirmingDisconnect}
        title={`Disconnect ${account.name}?`}
        description="You can reconnect at any time. Existing playlists on this service won't be deleted."
        confirmLabel="Disconnect"
        danger
        loading={disconnecting}
        onConfirm={() => void disconnect()}
        onCancel={() => setConfirmingDisconnect(false)}
      />}

      {!account.local_snapshot && <ConfirmDialog
        open={confirmingRemove}
        title={`Remove ${account.name}?`}
        description={`This permanently removes the ${accountId} profile: its credentials, token/cookie files and its imported library rows. Jobs and links that reference it will skip it. This can't be undone.`}
        confirmLabel="Remove account"
        danger
        loading={removing}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmingRemove(false)}
      />}

      {isLive && !isDefault && (
        <Modal open={renameOpen} onClose={() => setRenameOpen(false)} title={`Rename ${account.name}`}
          description="The account id stays stable — jobs and links keep pointing at this account.">
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              if (renameValue.trim()) void rename()
            }}
          >
            <TextField
              label="Account name"
              required
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setRenameOpen(false)}>Cancel</Button>
              <Button type="submit" loading={renaming} disabled={!renameValue.trim()}>Save</Button>
            </div>
          </form>
        </Modal>
      )}

      {isLive && isDefault && (
        <Modal open={addOpen} onClose={() => setAddOpen(false)} title={`Add a ${account.name} account`}
          description="A second profile with its own credentials, tokens and caches — connect it separately after creating it.">
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              if (addValue.trim()) void addAccount()
            }}
          >
            <TextField
              label="Account name"
              placeholder="e.g. Work, Family, Second profile"
              required
              autoFocus
              value={addValue}
              onChange={(e) => setAddValue(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setAddOpen(false)}>Cancel</Button>
              <Button type="submit" loading={adding} disabled={!addValue.trim()}>Create account</Button>
            </div>
          </form>
        </Modal>
      )}
    </Card>
  )
}
