import { AccountCard } from '@/components/accounts/AccountCard'
import { SonoraPanel } from '@/components/accounts/SonoraPanel'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingStatus, Skeleton } from '@/components/ui/Skeleton'
import { useAccounts } from '@/hooks/useAccounts'
import { useI18n } from '@/i18n/useI18n'

export default function Accounts() {
  const { t } = useI18n()
  const { accounts, loading, error, refresh } = useAccounts()

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-bold tracking-tight text-text sm:text-[22px]">{t('accounts.title')}</h1>
        <p className="text-[13.5px] text-text-3">
          {t('accounts.description')}
        </p>
      </div>

      {error && <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{t('accounts.loadError', { error })}</p>}

      <SonoraPanel />

      {loading && !accounts ? (
        <LoadingStatus label={t('accounts.loading')}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-40 w-full rounded-card" />
            ))}
          </div>
        </LoadingStatus>
      ) : accounts && accounts.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {accounts.map((account) => (
            <AccountCard key={account.id} account={account} onChanged={() => void refresh()} />
          ))}
        </div>
      ) : (
        <EmptyState title={t('accounts.emptyTitle')} description={t('accounts.emptyDescription')} />
      )}
    </div>
  )
}
