/**
 * navConfig — the SINGLE source of truth for the Portal sidebar.
 *
 * Both the Home screen (PortalHome) and every chrome-wrapped page
 * (PortalChromeLayout) render their sidebar from PORTAL_NAV_SECTIONS via the
 * shared <PortalSidebar>. Keeping one array here prevents the two sidebars from
 * drifting apart (the bug where Finance items differed between Home and the
 * budget/finance pages).
 *
 * Finance visibility is gated by the current user's effective permissions
 * (primary ∪ additional roles, served by GET /config/me/permissions), not
 * hardcoded role lists — see `permission` below and useRolePermissions().
 */
import {
  Home, ShoppingCart, Wallet, UserCheck, Settings, Database, Landmark, CalendarClock, ShieldCheck, GitBranch,
  Network, Repeat,
} from 'lucide-react'

// MRP has no single landing tile like the other modules — Portal links
// directly into three of its routes (Task 8 scaffold: Forecast, Consignment
// Stock, BOM Explorer), each permission-gated on its own. Resolved with
// import.meta.env directly (rather than via HrefContext, whose oa/vms/etc.
// fields are populated by PortalSidebar/PortalPageLayout/PortalHome) so this
// file is the single place that needs to change to wire the new module in.
export const MRP_URL = (import.meta.env.VITE_MRP_URL as string | undefined) || 'http://localhost:5179'

export interface NavItemDef {
  label: string
  icon: React.ComponentType<{ className?: string }>
  /**
   * One of:
   *  - 'portal:/path' → local Portal route
   *  - 'epms' / 'oa' / 'vms' → module landing (resolved with session handoff)
   *  - 'epms:/sub' → EPMS sub-route with session handoff
   *  - 'admin' → portal /admin route
   */
  href: string
  /** Permission key. Visible when perms[key] is true. */
  permission?: string
  /** Visible when the user has ANY of these permissions (e.g. a module
   *  whose pages span several permission keys). */
  anyPermission?: string[]
  /** Visible only to system_admin (used for portal-admin pages without a permission key). */
  adminOnly?: boolean
}

/** Permissions that grant access to the Finance module (finance + budget pages). */
export const FINANCE_ACCESS_PERMS = ['view_finance', 'view_budget_dashboard', 'view_budget_plans']

/** Permissions that grant access to the Booking module. */
export const BOOKING_ACCESS_PERMS = ['view_booking', 'manage_meeting_rooms']

export interface NavSectionDef {
  title?: string
  items: NavItemDef[]
}

export const PORTAL_NAV_SECTIONS: NavSectionDef[] = [
  {
    items: [
      { label: 'Home', icon: Home, href: 'portal:/' },
    ],
  },
  {
    // Applications, not pages. Each entry opens a module at its own root and
    // that module's own sidebar takes over from there. Do not add a deep link
    // to a single screen here — Agreements was listed for a while and it made
    // one EPMS page look like a sibling of EPMS itself, reachable from Portal
    // while every other EPMS page was not. It lives in the EPMS sidebar
    // (epms/src/components/layout/Sidebar.tsx), gated on the same permission.
    title: 'MODULES',
    items: [
      { label: 'Procurement', icon: ShoppingCart, href: 'epms' },
      { label: 'OA',          icon: Wallet,       href: 'oa' },
      { label: 'VMS',         icon: UserCheck,    href: 'vms' },
      { label: 'Finance',     icon: Landmark,     href: 'finance', anyPermission: FINANCE_ACCESS_PERMS },
      { label: 'Meeting Rooms', icon: CalendarClock, href: 'booking', anyPermission: BOOKING_ACCESS_PERMS },
      // MRP is a single module entry like its siblings — its own sidebar owns
      // the page-level navigation (Forecast / Consignment Stock / BOM Explorer).
      // Gated on mrp.report.view, not mrp.demand.write: every read the MRP
      // pages perform on load requires mrp.report.view, and packages/authz has
      // no write⇒read implication — a planner granted only mrp.demand.write
      // (as the nav previously advertised) would see the entry appear, then
      // have every fetch 403 forever (I5, final-phase review). mrp.demand.write
      // is still required for the in-page write actions.
      { label: 'MRP',         icon: Network,      href: 'mrp', permission: 'mrp.report.view' },
    ],
  },
  {
    title: 'ADMIN',
    items: [
      { label: 'Admin',            icon: Settings,    href: 'admin',                          adminOnly: true },
      { label: 'Data Maintenance', icon: Database,    href: 'portal:/admin/data-maintenance', permission: 'data_maintenance' },
      { label: 'Access Control',   icon: ShieldCheck, href: 'portal:/admin/access-control',   adminOnly: true },
      { label: 'Approval Routing', icon: GitBranch,   href: 'portal:/admin/approval-routing', adminOnly: true },
      { label: 'Approval Delegation', icon: Repeat,   href: 'portal:/admin/approval-delegation', adminOnly: true },
    ],
  },
]

/** Whether a nav item is visible for the given role + flat permissions map. */
export function isNavItemVisible(
  item: NavItemDef,
  userRole: string | null,
  perms: Record<string, boolean> | undefined,
): boolean {
  // system_admin sees everything.
  if (userRole === 'system_admin') return true
  if (item.adminOnly) return false
  if (item.anyPermission) {
    return item.anyPermission.some((p) => !!perms?.[p])
  }
  if (item.permission) {
    return !!perms?.[item.permission]
  }
  return true
}

export interface HrefContext {
  epmsHref: string
  oaHref: string
  vmsHref: string
  financeHref: string
  bookingHref: string
  /** base64 session for epms:/… sub-route handoff. */
  session: string
  epmsUrl: string
}

/** Resolve a nav item href key into a real URL. */
export function resolveNavHref(key: string, ctx: HrefContext): string {
  if (key === 'epms') return ctx.epmsHref
  if (key === 'oa')   return ctx.oaHref
  if (key === 'vms')  return ctx.vmsHref
  if (key === 'finance') return ctx.financeHref
  if (key === 'booking') return ctx.bookingHref
  // Bare 'mrp' lands on the module root, which redirects to its default page —
  // same shape as the sibling module entries above. The session fragment is
  // still needed because MRP is a separate origin.
  if (key === 'mrp') return ctx.session ? `${MRP_URL}/#__session=${ctx.session}` : MRP_URL
  if (key === 'admin') return '/admin'
  if (key.startsWith('portal:')) return key.slice('portal:'.length)
  if (key.startsWith('epms:')) {
    const path = key.slice('epms:'.length)
    return ctx.session ? `${ctx.epmsUrl}${path}#__session=${ctx.session}` : `${ctx.epmsUrl}${path}`
  }
  if (key.startsWith('mrp:')) {
    const path = key.slice('mrp:'.length)
    return ctx.session ? `${MRP_URL}${path}#__session=${ctx.session}` : `${MRP_URL}${path}`
  }
  return '#'
}
