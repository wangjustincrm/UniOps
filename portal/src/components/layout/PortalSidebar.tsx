/**
 * PortalSidebar — the single sidebar used by BOTH the Home screen and every
 * chrome-wrapped Portal page. Renders from the shared PORTAL_NAV_SECTIONS so the
 * two can never drift apart again. The frame itself is the Shell Sidebar; this
 * only turns the Portal nav config into its sections.
 */
import { Sidebar } from '@uniops/shell'
import { EPMS_URL } from '@/lib/api'
import { useBranding } from '@/hooks/useBranding'
import { PORTAL_NAV_SECTIONS, isNavItemVisible, resolveNavHref } from './navConfig'

interface PortalSidebarProps {
  /** Active item key — must match a nav item href (e.g. 'portal:/budget/plans'). */
  activeKey: string
  epmsHref: string
  oaHref: string
  vmsHref: string
  financeHref: string
  bookingHref: string
  /** base64 session for epms:/… sub-route handoff (may be empty). */
  session?: string
  userRole: string | null
  perms: Record<string, boolean> | undefined
  mobileOpen: boolean
  onClose: () => void
  collapsed: boolean
  onToggleCollapse: () => void
}

/** "MODULES" -> "Modules"; acronyms (MDM) stay as they are. */
const sectionTitle = (t: string) => (t.length <= 3 ? t : t.charAt(0) + t.slice(1).toLowerCase())

export function PortalSidebar({
  activeKey, epmsHref, oaHref, vmsHref, financeHref, bookingHref, session = '',
  userRole, perms, mobileOpen, onClose, collapsed, onToggleCollapse,
}: PortalSidebarProps) {
  const ctx = { epmsHref, oaHref, vmsHref, financeHref, bookingHref, session, epmsUrl: EPMS_URL }
  const { data: branding } = useBranding('portal')

  // Every entry is a full-page link (as before): Portal pages and the other
  // modules alike, with the session handed off in the URL where needed.
  const sections = PORTAL_NAV_SECTIONS.map((section) => ({
    title: section.title ? sectionTitle(section.title) : undefined,
    collapsible: section.collapsible ? { icon: <section.collapsible.icon /> } : undefined,
    items: section.items
      .filter((item) => isNavItemVisible(item, userRole, perms))
      .map((item) => ({
        label: item.label,
        href: resolveNavHref(item.href, ctx),
        icon: <item.icon />,
        external: true,
        sameTab: true,
        active: item.href === activeKey,
      })),
  }))

  return (
    <Sidebar
      brand={{
        name: branding?.name || 'UniOps',
        tagline: branding?.tagline || 'Admin Portal',
        logoUrl: branding?.logo_data_url || null,
      }}
      brandHref="/"
      sections={sections}
      collapsed={collapsed}
      onToggleCollapse={onToggleCollapse}
      mobileOpen={mobileOpen}
      onMobileClose={onClose}
    />
  )
}
