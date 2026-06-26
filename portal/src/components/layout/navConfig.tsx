/**
 * navConfig — the SINGLE source of truth for the Portal sidebar.
 *
 * Both the Home screen (PortalHome) and every chrome-wrapped page
 * (PortalChromeLayout) render their sidebar from PORTAL_NAV_SECTIONS via the
 * shared <PortalSidebar>. Keeping one array here prevents the two sidebars from
 * drifting apart (the bug where Finance items differed between Home and the
 * budget/finance pages).
 *
 * Finance visibility is gated by the EPMS Access Control Matrix (permission
 * keys, editable in EPMS → Admin Panel → Access Control Matrix), not hardcoded
 * role lists — see `permission` below and useRolePermissions().
 */
import {
  Home, ShoppingCart, Wallet, UserCheck, Settings, Database,
  BarChart2, CalendarRange, Layers, SlidersHorizontal, BookOpenCheck,
  Landmark, Banknote, ReceiptText, Scale, Percent, FileText, CreditCard,
} from 'lucide-react'
import type { RolePermissionMatrix } from '@/hooks/useRolePermissions'

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
  /** Access Control Matrix permission key. Visible when matrix[role][key] is true. */
  permission?: string
  /** Visible only to system_admin (used for portal-admin pages without a matrix key). */
  adminOnly?: boolean
}

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
    title: 'MODULES',
    items: [
      { label: 'Procurement', icon: ShoppingCart, href: 'epms' },
      { label: 'OA',          icon: Wallet,       href: 'oa' },
      { label: 'VMS',         icon: UserCheck,    href: 'vms' },
      { label: 'Finance',     icon: Landmark,     href: 'finance' },
    ],
  },
  {
    title: 'FINANCE',
    items: [
      { label: 'Budget Dashboard',     icon: BarChart2,         href: 'portal:/budget/dashboard',        permission: 'view_budget_dashboard' },
      { label: 'Budget Plans',         icon: CalendarRange,     href: 'portal:/budget/plans',            permission: 'view_budget_plans' },
      { label: 'Account Catalog',      icon: Layers,            href: 'portal:/budget/catalog',          permission: 'view_finance' },
      { label: 'Factor Library',       icon: Layers,            href: 'portal:/budget/factors',          permission: 'view_finance' },
      { label: 'Budget Config',        icon: SlidersHorizontal, href: 'portal:/budget/config',           permission: 'view_finance' },
      { label: 'Chart of Accounts',    icon: BookOpenCheck,     href: 'portal:/finance/coa',             permission: 'view_finance' },
      { label: 'Bank & Cards',         icon: CreditCard,        href: 'portal:/finance/bank-settings',   permission: 'view_finance' },
      { label: 'Bank Reconciliation',  icon: Landmark,          href: 'portal:/finance/bank',            permission: 'view_finance' },
      { label: 'Payment Batches',      icon: Banknote,          href: 'portal:/finance/payment-batches', permission: 'view_finance' },
      { label: 'Accounts Payable',     icon: FileText,          href: 'portal:/finance/ap',              permission: 'view_finance' },
      { label: 'Accounts Receivable',  icon: ReceiptText,       href: 'portal:/finance/ar',              permission: 'view_finance' },
      { label: 'General Ledger',       icon: Scale,             href: 'portal:/finance/gl',              permission: 'view_finance' },
      { label: 'Tax Settings',         icon: Percent,           href: 'portal:/finance/tax',             permission: 'view_finance' },
    ],
  },
  {
    title: 'ADMIN',
    items: [
      { label: 'Admin',            icon: Settings,  href: 'admin',                          adminOnly: true },
      { label: 'Data Maintenance', icon: Database,  href: 'portal:/admin/data-maintenance', adminOnly: true },
    ],
  },
]

/** Whether a nav item is visible for the given role + permission matrix. */
export function isNavItemVisible(
  item: NavItemDef,
  userRole: string | null,
  matrix: RolePermissionMatrix | undefined,
): boolean {
  // system_admin sees everything.
  if (userRole === 'system_admin') return true
  if (item.adminOnly) return false
  if (item.permission) {
    return userRole !== null && !!matrix?.[userRole]?.[item.permission]
  }
  return true
}

export interface HrefContext {
  epmsHref: string
  oaHref: string
  vmsHref: string
  financeHref: string
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
  if (key === 'admin') return '/admin'
  if (key.startsWith('portal:')) return key.slice('portal:'.length)
  if (key.startsWith('epms:')) {
    const path = key.slice('epms:'.length)
    return ctx.session ? `${ctx.epmsUrl}${path}#__session=${ctx.session}` : `${ctx.epmsUrl}${path}`
  }
  return '#'
}
