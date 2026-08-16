import { ACCOUNT_STATE_STYLES } from '@/lib/constants'
import type { MessageKey } from '@/i18n/messages'
import { useI18n } from '@/i18n/useI18n'
import type { AccountState } from '@/types'

import { Pill } from './Pill'

const STATE_LABEL_KEYS: Record<AccountState, MessageKey> = {
  connected: 'status.connected',
  expired: 'status.expired',
  error: 'status.error',
  unconfigured: 'status.unconfigured',
}

/** An account's connection state, via the shared Pill primitive. */
export function StatusPill({ state, className }: { state: AccountState; className?: string }) {
  const { t } = useI18n()
  const style = ACCOUNT_STATE_STYLES[state]
  return <Pill toneClasses={style.badge} label={t(STATE_LABEL_KEYS[state])} className={className} />
}
