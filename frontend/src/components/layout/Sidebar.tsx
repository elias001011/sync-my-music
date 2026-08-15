import { useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import {
  LuArrowLeftRight,
  LuChartBar,
  LuDatabase,
  LuGithub,
  LuLayoutDashboard,
  LuLink2,
  LuListMusic,
  LuMenu,
  LuPanelLeftClose,
  LuPanelLeftOpen,
  LuRefreshCw,
  LuScrollText,
  LuSettings2,
  LuX,
} from 'react-icons/lu'
import type { IconType } from 'react-icons'

import songmirrorMark from '@/assets/brand/songmirror-mark.png'
import { useSidebarCollapsed } from '@/hooks/useSidebarCollapsed'
import type { MessageKey } from '@/i18n/messages'
import { useI18n } from '@/i18n/useI18n'
import { cn } from '@/lib/cn'

const REPO_URL = 'https://github.com/elias001011/sync-my-music'

const NAV_ITEMS: Array<{ to: string; labelKey: MessageKey; end: boolean; icon: IconType }> = [
  { to: '/', labelKey: 'nav.dashboard', end: true, icon: LuLayoutDashboard },
  { to: '/library', labelKey: 'nav.library', end: false, icon: LuDatabase },
  { to: '/recaps', labelKey: 'nav.recaps', end: false, icon: LuChartBar },
  { to: '/accounts', labelKey: 'nav.accounts', end: false, icon: LuLink2 },
  { to: '/playlists', labelKey: 'nav.playlists', end: false, icon: LuListMusic },
  { to: '/sync', labelKey: 'nav.sync', end: false, icon: LuRefreshCw },
  { to: '/transfers', labelKey: 'nav.transfers', end: false, icon: LuArrowLeftRight },
  { to: '/settings', labelKey: 'nav.settings', end: false, icon: LuSettings2 },
  { to: '/logs', labelKey: 'nav.logs', end: false, icon: LuScrollText },
]

/** 240px persistent rail from `lg` (1024px) up — collapsible to a 68px
 * icon-only rail (tooltip labels), state persisted to localStorage. Below
 * `lg`, the rail is replaced by a slim top bar whose hamburger opens the
 * same nav as a dropdown drawer. */
export function Sidebar() {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed()
  const [menuOpen, setMenuOpen] = useState(false)
  const { t } = useI18n()

  useEffect(() => {
    if (!menuOpen) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    const mql = window.matchMedia('(min-width: 1024px)')
    function onBreakpointChange() {
      if (mql.matches) setMenuOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    mql.addEventListener('change', onBreakpointChange)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      mql.removeEventListener('change', onBreakpointChange)
    }
  }, [menuOpen])

  return (
    <>
      {/* Desktop rail */}
      <aside
        className={cn(
          // The shell stretches with the flex row so the rail's surface reaches the
          // page bottom even when content runs taller than the viewport; the inner
          // column is the sticky, viewport-height part that holds and scrolls the nav.
          'hidden shrink-0 border-r border-border bg-surface transition-[width] duration-base lg:block',
          collapsed ? 'w-[68px]' : 'w-60',
        )}
      >
        <div className="sticky top-0 flex h-dvh flex-col overflow-y-auto">
        <div
          className={cn(
            'flex h-16 shrink-0 items-center border-b border-border',
            collapsed ? 'justify-center px-2' : 'gap-2.5 px-4',
          )}
        >
          {collapsed ? (
            <button
              type="button"
              onClick={toggleCollapsed}
              title={t('nav.expandSidebar')}
              aria-label={t('nav.expandSidebar')}
              className="group grid size-9 place-items-center rounded-control transition-colors duration-fast hover:bg-surface-2"
            >
              {/* logo by default; the expand affordance cross-fades in on hover */}
              <span className="col-start-1 row-start-1 transition-opacity duration-fast group-hover:opacity-0">
                <Logo />
              </span>
              <LuPanelLeftOpen
                className="col-start-1 row-start-1 size-[18px] text-text-2 opacity-0 transition-opacity duration-fast group-hover:opacity-100"
                aria-hidden="true"
              />
            </button>
          ) : (
            <>
              <Link
                to="/"
                title={t('nav.dashboard')}
                className="flex min-w-0 items-center gap-2.5 rounded-control transition-opacity duration-fast hover:opacity-75"
              >
                <Logo />
                <span className="truncate text-[15px] font-extrabold tracking-tight text-text">Sync My Music</span>
              </Link>
              <button
                type="button"
                onClick={toggleCollapsed}
                title={t('nav.collapseSidebar')}
                aria-label={t('nav.collapseSidebar')}
                className="ml-auto flex size-9 shrink-0 items-center justify-center rounded-control text-text-2 transition-colors duration-fast hover:bg-surface-2 hover:text-text"
              >
                <LuPanelLeftClose className="size-[18px]" aria-hidden="true" />
              </button>
            </>
          )}
        </div>

        <nav aria-label={t('nav.primary')} className="flex flex-1 flex-col gap-1 p-3">
          {!collapsed && (
            <span className="px-2.5 pb-1.5 font-mono text-[10px] font-bold tracking-[0.14em] text-text-3">{t('nav.menu')}</span>
          )}
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={collapsed ? t(item.labelKey) : undefined}
              aria-label={collapsed ? t(item.labelKey) : undefined}
              className={({ isActive }) =>
                cn(
                  'flex h-10 items-center gap-2.5 rounded-[9px] text-sm font-medium transition-colors duration-fast',
                  collapsed ? 'justify-center px-0' : 'px-3',
                  isActive
                    ? 'bg-accent-soft font-semibold text-accent shadow-[inset_2px_0_0_var(--color-accent)]'
                    : 'text-text-2 hover:bg-surface-2 hover:text-text',
                )
              }
            >
              <item.icon className="size-[18px] shrink-0" aria-hidden="true" />
              {!collapsed && t(item.labelKey)}
            </NavLink>
          ))}
        </nav>

        {/* The nav above takes flex-1, so this sits on the rail's bottom edge. */}
        <div className="shrink-0 border-t border-border p-3">
          <RepoLink />
        </div>
        </div>
      </aside>

      {/* Mobile: slim top bar + dropdown drawer */}
      <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-2.5 border-b border-border bg-surface/90 px-4 backdrop-blur lg:hidden">
        <Link
          to="/"
          title={t('nav.dashboard')}
          onClick={() => setMenuOpen(false)}
          className="flex min-w-0 items-center gap-2.5 rounded-control transition-opacity duration-fast hover:opacity-75"
        >
          <Logo />
          <span className="truncate text-[14.5px] font-extrabold tracking-tight text-text">Sync My Music</span>
        </Link>
        <button
          type="button"
          className="ml-auto inline-flex size-11 shrink-0 items-center justify-center rounded-control border border-border bg-surface-2 text-text"
          aria-expanded={menuOpen}
          aria-controls="mobile-nav"
          aria-label={menuOpen ? t('nav.closeMenu') : t('nav.openMenu')}
          onClick={() => setMenuOpen((v) => !v)}
        >
          {menuOpen ? <LuX className="size-5" aria-hidden="true" /> : <LuMenu className="size-5" aria-hidden="true" />}
        </button>
      </header>

      {menuOpen && (
        <nav
          id="mobile-nav"
          aria-label={t('nav.primary')}
          className="sticky top-14 z-40 flex flex-col gap-0.5 border-b border-border-strong bg-surface px-3 pb-4 pt-2.5 lg:hidden"
        >
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex h-12 items-center gap-3 rounded-[9px] pl-3.5 text-[15px] font-medium transition-colors duration-fast',
                  isActive ? 'bg-accent-soft font-semibold text-accent' : 'text-text-2 hover:bg-surface-2',
                )
              }
            >
              <item.icon className="size-[18px] shrink-0" aria-hidden="true" />
              {t(item.labelKey)}
            </NavLink>
          ))}
          <div className="mt-2 border-t border-border pt-3">
            <RepoLink />
          </div>
        </nav>
      )}
    </>
  )
}

/** Source link, centered on the sidebar's bottom edge. Icon-only in both rail
 * widths, so collapsing the rail doesn't change it. */
function RepoLink() {
  const { t } = useI18n()
  return (
    <div className="flex items-center justify-center">
      <a
        href={REPO_URL}
        target="_blank"
        rel="noreferrer"
        title={t('nav.github')}
        aria-label={t('nav.github')}
        className="flex size-9 items-center justify-center rounded-control text-text-3 transition-colors duration-fast hover:bg-surface-2 hover:text-text"
      >
        <LuGithub className="size-[18px]" aria-hidden="true" />
      </a>
    </div>
  )
}

function Logo() {
  // Orange mark on a transparent background — theme-agnostic, no light/dark
  // swap needed.
  return <img src={songmirrorMark} alt="" className="size-7 shrink-0" />
}
