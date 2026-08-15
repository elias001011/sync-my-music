import { useState } from 'react'

import { Button } from './Button'
import { useI18n } from '@/i18n/useI18n'

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const { t } = useI18n()

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API can be unavailable (insecure context/permissions) — the
      // value is still plain selectable text, so this is a soft failure.
    }
  }

  return (
    <Button variant="secondary" onClick={() => void handleCopy()} className="shrink-0">
      {copied ? t('common.copied') : (label ?? t('common.copy'))}
    </Button>
  )
}
