import { useDarkMode } from '@/hooks/useDarkMode'
import { useI18n } from '@/i18n/useI18n'

import { Toggle } from '../ui/Toggle'

/** Light/dark switch. Lives on the Settings page; applies instantly and persists
 * to localStorage (see useDarkMode). Dark is the default. */
export function ThemeToggle() {
  const [dark, toggle] = useDarkMode()
  const { t } = useI18n()
  return <Toggle checked={dark} onChange={toggle} label={t('settings.theme')} />
}
