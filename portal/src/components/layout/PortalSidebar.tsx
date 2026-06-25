/**
 * PortalSidebar — the single sidebar used by BOTH the Home screen and every
 * chrome-wrapped Portal page. Renders from the shared PORTAL_NAV_SECTIONS so the
 * two can never drift apart again.
 */
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { EPMS_URL } from '@/lib/api'
import { useBranding } from '@/hooks/useBranding'
import type { RolePermissionMatrix } from '@/hooks/useRolePermissions'
import {
  PORTAL_NAV_SECTIONS, isNavItemVisible, resolveNavHref,
} from './navConfig'

interface PortalSidebarProps {
  /** Active item key — must match a nav item href (e.g. 'portal:/budget/plans'). */
  activeKey: string
  epmsHref: string
  oaHref: string
  vmsHref: string
  /** base64 session for epms:/… sub-route handoff (may be empty). */
  session?: string
  userRole: string | null
  matrix: RolePermissionMatrix | undefined
  mobileOpen: boolean
  onClose: () => void
  collapsed: boolean
  onToggleCollapse: () => void
}

export function PortalSidebar({
  activeKey, epmsHref, oaHref, vmsHref, session = '',
  userRole, matrix, mobileOpen, onClose, collapsed, onToggleCollapse,
}: PortalSidebarProps) {
  const ctx = { epmsHref, oaHref, vmsHref, session, epmsUrl: EPMS_URL }

  const { data: branding } = useBranding('portal')
  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Admin Portal'
  const logoUrl = branding?.logo_data_url || null
  const initials =
    brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'U'

  const logoMark = logoUrl ? (
    <img
      src={logoUrl}
      alt={brandName}
      className="h-8 w-8 shrink-0 rounded-lg bg-white/15 object-contain p-0.5"
    />
  ) : (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/15 text-white font-bold text-sm">
      {initials}
    </div>
  )

  return (
    <aside className={cn(
      'flex h-screen flex-col bg-[#085E5E] transition-all duration-200 shrink-0',
      collapsed ? 'w-16' : 'w-56',
      'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-2xl max-md:w-56',
      mobileOpen ? 'max-md:translate-x-0 max-md:opacity-100' : 'max-md:-translate-x-full max-md:opacity-0',
    )}>
      {/* Logo + collapse toggle */}
      <div className="flex h-[60px] items-center justify-between border-b border-white/10 px-3 gap-2">
        {!collapsed ? (
          <Link to="/" className="flex items-center gap-2.5 min-w-0">
            {logoMark}
            <div className="leading-tight min-w-0">
              <p className="truncate text-sm font-bold text-white tracking-tight">{brandName}</p>
              <p className="truncate text-[9px] font-semibold uppercase tracking-widest text-white/50">{brandTagline}</p>
            </div>
          </Link>
        ) : (
          <Link to="/" className="mx-auto" title={brandName}>
            {logoMark}
          </Link>
        )}
        <button
          onClick={onToggleCollapse}
          className="hidden md:flex shrink-0 rounded p-1 text-white/50 hover:bg-white/10 hover:text-white/90 transition-colors"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-4">
        {PORTAL_NAV_SECTIONS.map((section, idx) => {
          const visibleItems = section.items.filter((item) => isNavItemVisible(item, userRole, matrix))
          if (visibleItems.length === 0) return null
          return (
            <div key={section.title ?? `s${idx}`} className="mb-4">
              {!collapsed && section.title && (
                <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                  {section.title}
                </p>
              )}
              {collapsed && idx > 0 && <div className="my-2 border-t border-white/10" />}
              <div className="space-y-0.5">
                {visibleItems.map((item) => {
                  const Icon = item.icon
                  const href = resolveNavHref(item.href, ctx)
                  const isActive = item.href === activeKey
                  return (
                    <a key={item.label} href={href} onClick={onClose}>
                      <div
                        title={collapsed ? item.label : undefined}
                        className={cn(
                          'flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-colors',
                          collapsed && 'justify-center px-0',
                          isActive
                            ? 'bg-white/15 text-white font-medium'
                            : 'text-white/65 hover:bg-white/10 hover:text-white cursor-pointer',
                        )}>
                        <Icon className="h-4 w-4 shrink-0" />
                        {!collapsed && <span className="flex-1">{item.label}</span>}
                      </div>
                    </a>
                  )
                })}
              </div>
            </div>
          )
        })}
      </nav>
    </aside>
  )
}
