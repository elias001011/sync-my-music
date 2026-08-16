import { useMemo, useState } from 'react'

import { LinkCard } from '@/components/playlists/LinkCard'
import { LinkEditorModal } from '@/components/playlists/LinkEditorModal'
import { MusifyExportCard } from '@/components/playlists/MusifyExportCard'
import { PlaylistVersionCard } from '@/components/playlists/PlaylistVersionCard'
import { ProviderPlaylistsCard } from '@/components/playlists/ProviderPlaylistsCard'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingStatus, Skeleton } from '@/components/ui/Skeleton'
import { useAccounts } from '@/hooks/useAccounts'
import { useLinks } from '@/hooks/useLinks'
import { useProviderPlaylists } from '@/hooks/useProviderPlaylists'
import { useI18n } from '@/i18n/useI18n'
import type { PlaylistLink } from '@/types'

export default function Playlists() {
  const { t } = useI18n()
  const { accounts, loading: accountsLoading, error: accountsError } = useAccounts()
  const connectedAccounts = useMemo(() => accounts?.filter((a) => a.state === 'connected') ?? [], [accounts])
  const syncAccounts = useMemo(() => connectedAccounts.filter((a) => a.transferable), [connectedAccounts])
  const connectedIds = useMemo(() => connectedAccounts.map((a) => a.id), [connectedAccounts])
  const { entries } = useProviderPlaylists(connectedIds)
  const { links, loading: linksLoading, error: linksError, refresh: refreshLinks } = useLinks()

  const [editorTarget, setEditorTarget] = useState<PlaylistLink | 'new' | null>(null)

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-text sm:text-[22px]">{t('playlists.title')}</h1>
        <p className="mt-1 text-sm text-text-3">{t('playlists.description')}</p>
      </div>

      <section className="flex flex-col gap-4">
        <h2 className="text-[17px] font-bold text-text">{t('playlists.browse')}</h2>

        {accountsError && (
          <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{t('accounts.loadError', { error: accountsError })}</p>
        )}

        {accountsLoading && !accounts ? (
          <LoadingStatus label={t('accounts.loading')}>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-40 w-full rounded-card" />
              ))}
            </div>
          </LoadingStatus>
        ) : accounts && accounts.length > 0 ? (
          <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {accounts.map((account) => (
              <ProviderPlaylistsCard key={account.id} account={account} entry={entries[account.id]} />
            ))}
          </div>
        ) : (
          <EmptyState title={t('accounts.emptyTitle')} description={t('accounts.emptyDescription')} />
        )}
      </section>

      <section>
        <MusifyExportCard accounts={connectedAccounts} entries={entries} />
      </section>

      <section>
        <PlaylistVersionCard accounts={connectedAccounts} entries={entries} />
      </section>

      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-baseline gap-2.5">
            <h2 className="text-[17px] font-bold text-text">{t('playlists.pairings')}</h2>
            {links && (
              <span className="font-mono text-[11.5px] text-text-3">
                {t('playlists.linkCount', { count: links.length, active: links.filter((link) => link.enabled).length })}
              </span>
            )}
          </div>
          <Button
            onClick={() => setEditorTarget('new')}
            disabled={syncAccounts.length < 2}
            title={syncAccounts.length < 2 ? t('playlists.connectTwo') : undefined}
          >
            {t('playlists.newPairing')}
          </Button>
        </div>
        <p className="text-sm text-text-3">
          {t('playlists.pairingDescription')}
        </p>

        {linksError && (
          <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{t('playlists.loadError', { error: linksError })}</p>
        )}

        {linksLoading && !links ? (
          <LoadingStatus label={t('playlists.loading')}>
            <div className="flex flex-col gap-3">
              {[0, 1].map((i) => (
                <Skeleton key={i} className="h-24 w-full rounded-card" />
              ))}
            </div>
          </LoadingStatus>
        ) : links && links.length > 0 ? (
          <div className="flex flex-col gap-3">
            {links.map((link) => (
              <LinkCard
                key={link.id}
                link={link}
                accounts={syncAccounts}
                playlistEntries={entries}
                onEdit={() => setEditorTarget(link)}
                onChanged={() => void refreshLinks()}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            title={t('playlists.emptyTitle')}
            description={t('playlists.emptyDescription')}
          />
        )}
      </section>

      <LinkEditorModal
        open={editorTarget !== null}
        onClose={() => setEditorTarget(null)}
        link={editorTarget === 'new' ? null : editorTarget}
        accounts={syncAccounts}
        playlistEntries={entries}
        onSaved={() => {
          setEditorTarget(null)
          void refreshLinks()
        }}
      />
    </div>
  )
}
