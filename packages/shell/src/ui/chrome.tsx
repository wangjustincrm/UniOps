/**
 * App chrome: AppShell frame, Sidebar, Topbar, UserMenu.
 *
 * Every UniOps module renders the same frame from a config — the app supplies
 * its nav sections (already filtered by permission), branding and user; the
 * look lives here. Re-skinning the sidebar for all modules = edit this file.
 */
import React, { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { ArrowLeft, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, LogOut, Menu, User } from 'lucide-react'
import { cn } from '../lib/cn'

export interface NavItem {
  label: string
  href: string
  /** lucide icon element, sized by the sidebar. */
  icon?: React.ReactNode
  /** Leaves the app (full URL or another module). Opens in a new tab unless sameTab. */
  external?: boolean
  sameTab?: boolean
  /** Count shown at the end of the row (e.g. open tasks). */
  badge?: number
  /** Extra paths that also mark this item active. */
  activePrefixes?: string[]
  /** Set by apps that track the current item themselves (Portal's activeKey);
   *  overrides route matching when defined. */
  active?: boolean
}

export interface NavSection {
  /** Sentence case, e.g. "Master data". Omit for the first, untitled group. */
  title?: string
  items: NavItem[]
  /** Render as one expandable row (label = title, this icon) instead of a heading
   *  with the items always listed. Opens by itself when one of its items is active;
   *  in the icon-only sidebar the items are listed directly. */
  collapsible?: { icon: React.ReactNode }
}

export interface Branding {
  name: string
  tagline?: string | null
  logoUrl?: string | null
}

function initials(name: string) {
  return name.split(/\s+/).map((w) => w[0]).join('').toUpperCase().slice(0, 2)
}

function BrandMark({ brand, size = 'md' }: { brand: Branding; size?: 'md' | 'lg' }) {
  const box = size === 'lg' ? 'h-10 w-10' : 'h-8 w-8'
  return brand.logoUrl ? (
    <span className={cn('flex shrink-0 items-center justify-center overflow-hidden rounded-md bg-surface', box)}>
      <img src={brand.logoUrl} alt="" className="h-full w-full object-contain p-0.5" />
    </span>
  ) : (
    <span className={cn('flex shrink-0 items-center justify-center rounded-md bg-white/15 text-xs font-semibold text-white', box)}>
      {initials(brand.name)}
    </span>
  )
}

export interface SidebarProps {
  brand: Branding
  sections: NavSection[]
  collapsed?: boolean
  onToggleCollapse?: () => void
  mobileOpen?: boolean
  onMobileClose?: () => void
  /** "Back to Portal" link; omit inside Portal itself. */
  portalUrl?: string
  /** Rows pinned above the footer (e.g. module switcher). */
  footer?: React.ReactNode
  /** In-app route the brand links to (e.g. "/" for Portal home). */
  brandHref?: string
}

function Count({ n, collapsed }: { n: number; collapsed?: boolean }) {
  const text = n > 99 ? '99+' : String(n)
  return collapsed ? (
    <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-danger-400" aria-label={`${text} open`} />
  ) : (
    <span className="ml-auto font-mono text-xs tabular-nums text-nav-fg">{text}</span>
  )
}

export function Sidebar({ brand, sections, collapsed, onToggleCollapse, mobileOpen, onMobileClose, portalUrl, footer, brandHref }: SidebarProps) {
  const location = useLocation()
  const row = 'group relative flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors duration-150'
  const isActive = (item: NavItem, linkActive: boolean) => item.active ?? (
    linkActive || location.pathname.startsWith(item.href + '/') || !!item.activePrefixes?.some((p) => location.pathname.startsWith(p)))
  // Collapsible groups: open when the current page is inside, else closed until clicked.
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() => Object.fromEntries(
    sections.filter((s) => s.collapsible && s.title).map((s) => [s.title!, s.items.some((i) => isActive(i, location.pathname === i.href))])))

  const renderItem = (item: NavItem, nested = false) => {
    const content = (active: boolean) => (
      <>
        {item.icon && <span className="flex w-4 shrink-0 items-center justify-center [&>svg]:h-4 [&>svg]:w-4">{item.icon}</span>}
        {!collapsed && <span className="truncate">{item.label}</span>}
        {!collapsed && item.external && !item.sameTab && <ExternalLink aria-hidden className="ml-auto h-3.5 w-3.5 opacity-60" />}
        {!!item.badge && <Count n={item.badge} collapsed={collapsed} />}
        {active && <span aria-hidden className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-white/80" />}
      </>
    )
    const cls = (active: boolean) => cn(
      row,
      nested && !collapsed && 'pl-9',
      active ? 'bg-nav-item-active font-medium text-nav-fg-active' : 'text-nav-fg hover:bg-nav-item-hover hover:text-nav-fg-active',
      collapsed && 'justify-center px-0',
    )
    if (item.external) {
      const active = item.active ?? false
      return (
        <a key={item.href} href={item.href} title={collapsed ? item.label : undefined}
          target={item.sameTab ? undefined : '_blank'} rel={item.sameTab ? undefined : 'noopener noreferrer'}
          aria-current={active ? 'page' : undefined}
          className={cls(active)} onClick={onMobileClose}>
          {content(active)}
        </a>
      )
    }
    return (
      <NavLink key={item.href} to={item.href} title={collapsed ? item.label : undefined} onClick={onMobileClose}
        className={({ isActive: a }) => cls(isActive(item, a))}>
        {({ isActive: a }) => content(isActive(item, a))}
      </NavLink>
    )
  }

  return (
    <>
      {mobileOpen && <div className="fixed inset-0 z-20 bg-inverse/40 md:hidden" onClick={onMobileClose} aria-hidden />}
      <aside
        className={cn(
          'flex h-screen shrink-0 flex-col bg-nav transition-[width,transform] duration-200',
          collapsed ? 'w-16' : 'w-60',
          'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:w-60',
          mobileOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full',
        )}
      >
        <div className={cn('flex h-15 items-center gap-2 border-b border-nav-line px-3', collapsed ? 'justify-center' : 'justify-between')}>
          {(() => {
            const inner = (
              <>
                <BrandMark brand={brand} />
                {!collapsed && (
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold leading-tight text-nav-fg-active">{brand.name}</p>
                    {brand.tagline && <p className="truncate text-2xs text-nav-fg">{brand.tagline}</p>}
                  </div>
                )}
              </>
            )
            return brandHref
              ? <Link to={brandHref} title={brand.name} className="flex min-w-0 items-center gap-2.5 rounded-md">{inner}</Link>
              : <div className="flex min-w-0 items-center gap-2.5">{inner}</div>
          })()}
          {!collapsed && onToggleCollapse && (
            <button type="button" onClick={onToggleCollapse} aria-label="Collapse sidebar"
              className="shrink-0 rounded p-1 text-nav-fg transition-colors hover:bg-nav-item-hover hover:text-nav-fg-active max-md:hidden">
              <ChevronLeft className="h-4 w-4" />
            </button>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto px-2 py-3 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" aria-label="Main">
          {sections.filter((s) => s.items.length > 0).map((section, si) => {
            if (section.collapsible && section.title && !collapsed) {
              const title = section.title
              const open = !!openGroups[title]
              return (
                <div key={title} className={cn(si > 0 && 'mt-5', 'flex flex-col gap-0.5')}>
                  <button type="button" aria-expanded={open}
                    onClick={() => setOpenGroups((g) => ({ ...g, [title]: !g[title] }))}
                    className={cn(row, 'w-full text-nav-fg hover:bg-nav-item-hover hover:text-nav-fg-active')}>
                    <span className="flex w-4 shrink-0 items-center justify-center [&>svg]:h-4 [&>svg]:w-4">{section.collapsible.icon}</span>
                    <span className="flex-1 truncate text-left">{title}</span>
                    <ChevronDown aria-hidden className={cn('h-4 w-4 shrink-0 transition-transform', !open && '-rotate-90')} />
                  </button>
                  {open && section.items.map((item) => renderItem(item, true))}
                </div>
              )
            }
            return (
              <div key={section.title ?? si} className={cn(si > 0 && 'mt-5')}>
                {section.title && !collapsed && (
                  <p className="mb-1.5 px-2.5 text-2xs font-medium text-nav-fg">{section.title}</p>
                )}
                {section.title && collapsed && si > 0 && <div className="mx-2 mb-2 border-t border-nav-line" />}
                <div className="flex flex-col gap-0.5">
                  {section.items.map((item) => renderItem(item))}
                </div>
              </div>
            )
          })}
        </nav>

        {footer && <div className="border-t border-nav-line p-2">{footer}</div>}

        {portalUrl && (
          <div className="border-t border-nav-line p-2">
            <a href={portalUrl} title="Back to UniOps Portal"
              className={cn(row, 'text-nav-fg hover:bg-nav-item-hover hover:text-nav-fg-active', collapsed && 'justify-center px-0')}>
              <ArrowLeft className="h-4 w-4 shrink-0" />
              {!collapsed && <span>Back to Portal</span>}
            </a>
          </div>
        )}

        {collapsed && onToggleCollapse && (
          <div className="border-t border-nav-line p-2 max-md:hidden">
            <button type="button" onClick={onToggleCollapse} aria-label="Expand sidebar"
              className="flex h-9 w-full items-center justify-center rounded-md text-nav-fg transition-colors hover:bg-nav-item-hover hover:text-nav-fg-active">
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        )}
      </aside>
    </>
  )
}

export interface UserMenuItem {
  label: string
  icon?: React.ReactNode
  onSelect?: () => void
  href?: string
  tone?: 'default' | 'danger'
}

export interface UserMenuProps {
  name: string
  /** Role / department line under the name. */
  subtitle?: string | null
  items?: UserMenuItem[]
  onSignOut?: () => void
}

export function UserMenu({ name, subtitle, items = [], onSignOut }: UserMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [open])
  const all: UserMenuItem[] = [
    ...items,
    ...(onSignOut ? [{ label: 'Sign out', icon: <LogOut />, onSelect: onSignOut, tone: 'danger' as const }] : []),
  ]
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}
        className="flex h-9 items-center gap-2 rounded-lg px-2 text-sm transition-colors hover:bg-surface-sunken">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-soft text-xs font-medium text-accent-fg">
          {initials(name) || <User className="h-4 w-4" />}
        </span>
        <span className="hidden min-w-0 text-left sm:block">
          <span className="block max-w-40 truncate text-sm font-medium leading-tight text-fg">{name}</span>
          {subtitle && <span className="block max-w-40 truncate text-xs leading-tight text-fg-muted">{subtitle}</span>}
        </span>
        <ChevronDown aria-hidden className="h-4 w-4 text-fg-faint" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-40 mt-1 w-56 rounded-lg border border-line-subtle bg-surface p-1 shadow-md animate-[modal-in_120ms_var(--ease-standard)]">
          {all.map((it) => {
            const cls = cn(
              'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors [&>svg]:h-4 [&>svg]:w-4',
              it.tone === 'danger' ? 'text-danger-fg hover:bg-danger-soft' : 'text-fg-secondary hover:bg-surface-subtle hover:text-fg',
            )
            return it.href ? (
              <a key={it.label} role="menuitem" href={it.href} className={cls} onClick={() => setOpen(false)}>{it.icon}{it.label}</a>
            ) : (
              <button key={it.label} role="menuitem" type="button" className={cls} onClick={() => { setOpen(false); it.onSelect?.() }}>
                {it.icon}{it.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export interface TopbarProps {
  /** Left: page context / breadcrumbs / search. */
  start?: React.ReactNode
  /** Right: notifications, help, UserMenu. */
  end?: React.ReactNode
  onMobileMenu?: () => void
  className?: string
}

export function Topbar({ start, end, onMobileMenu, className }: TopbarProps) {
  return (
    <header className={cn('flex h-15 shrink-0 items-center gap-3 border-b border-line-subtle bg-surface px-4', className)}>
      {onMobileMenu && (
        <button type="button" onClick={onMobileMenu} aria-label="Open menu"
          className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-surface-sunken md:hidden">
          <Menu className="h-5 w-5" />
        </button>
      )}
      <div className="flex min-w-0 flex-1 items-center gap-3">{start}</div>
      {end && <div className="flex shrink-0 items-center gap-1.5">{end}</div>}
    </header>
  )
}

/**
 * The frame: sidebar on the left, topbar + content on the right.
 * `embedded` (module shown inside Portal's iframe) drops both bars.
 */
export function AppShell({ sidebar, topbar, embedded, children, className }: {
  sidebar?: React.ReactNode
  topbar?: React.ReactNode
  embedded?: boolean
  children: React.ReactNode
  className?: string
}) {
  if (embedded) return <div className={cn('min-h-screen bg-canvas', className)}>{children}</div>
  return (
    <div className={cn('flex h-screen overflow-hidden bg-canvas', className)}>
      {sidebar}
      <div className="flex min-w-0 flex-1 flex-col">
        {topbar}
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  )
}

/** Remember the sidebar's collapsed state per app. */
export function useSidebarCollapsed(storageKey: string): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(storageKey) === '1' } catch { return false }
  })
  const toggle = () => setCollapsed((c) => {
    const next = !c
    try { localStorage.setItem(storageKey, next ? '1' : '0') } catch { /* private mode */ }
    return next
  })
  return [collapsed, toggle]
}
