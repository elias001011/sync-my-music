import { useState } from 'react'

import { api, errorMessage } from '@/api'
import { cn } from '@/lib/cn'
import { serviceLogoId, tagDot, tagText } from '@/lib/constants'
import type { Account, AuthKind } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { ConfirmDialog } from '../ui/ConfirmDialog'
import { ServiceLogo } from '../ui/ServiceLogo'
import { StatusPill } from '../ui/StatusPill'
import { ConnectWizardModal } from './ConnectWizardModal'

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
  const [error, setError] = useState<string | null>(null)

  const isConnected = account.state === 'connected' || account.state === 'expired'
  const visualProvider = account.provider ?? account.id
  const logoId = serviceLogoId(visualProvider)

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

  async function toggleEnabled() {
    setError(null)
    try {
      await api.setAccountEnabled(account.id, account.enabled === false)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
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
        <h3 className="text-base font-bold text-text">{account.name}</h3>
        <span className="font-mono text-[10px] tracking-wide text-text-3">{account.local_snapshot ? 'LOCAL BACKUP' : AUTH_KIND_LABELS[account.auth_kind]}</span>
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

      {error && <p className="text-xs text-danger">{error}</p>}

      {!account.local_snapshot && <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button variant={isConnected ? 'secondary' : 'primary'} size="sm" onClick={() => setWizardOpen(true)}>
          {isConnected ? 'Reconnect' : 'Connect'}
        </Button>
        {isConnected && (
          <Button variant="ghost" size="sm" onClick={() => setConfirmingDisconnect(true)}>
            Disconnect
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => void toggleEnabled()}>
          {account.enabled === false ? 'Enable connector' : 'Pause connector'}
        </Button>
      </div>}

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
    </Card>
  )
}
