import { Sidebar as ShellSidebar, useSidebarCollapsed } from '@uniops/shell'
import {
  LayoutDashboard,
  CheckSquare,
  ClipboardList,
  Package,
  Warehouse,
  FileText,
  CreditCard,
  FolderOpen,
  Building2,
  FolderTree,
  Wrench,
  FileSignature,
  Receipt,
  Settings,
  Truck,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth.store'
import { useConfig, useRolePermissions } from '@/hooks/useConfig'
import type { UserRole } from '@/types'
import type { RolePermissions } from '@/services/config'

interface NavSection {
  title: string
  items: NavItemDef[]
}

interface NavItemDef {
  label: string
  href: string
  icon: React.ReactNode
  badge?: number
  roles?: UserRole[]
  permission?: keyof RolePermissions
  /** Visible when the user holds ANY of these keys (system_admin always). */
  anyPermission?: string[]
}

const NAV_SECTIONS: NavSection[] = [
  {
    title: 'MY WORKSPACE',
    items: [
      { label: 'Dashboard', href: '/dashboard', icon: <LayoutDashboard className="h-4 w-4" /> },
      { label: 'Task Inbox', href: '/tasks', icon: <CheckSquare className="h-4 w-4" /> },
      { label: 'Reports', href: '/reports', icon: <FolderOpen className="h-4 w-4" /> },
    ],
  },
  {
    title: 'PROCUREMENT',
    items: [
      { label: 'Purchase Requisitions', href: '/pr', icon: <ClipboardList className="h-4 w-4" />, permission: 'view_pr' },
      { label: 'Purchase Orders', href: '/po', icon: <Package className="h-4 w-4" />, permission: 'view_po' },
      { label: 'Goods Receipt', href: '/gr', icon: <Warehouse className="h-4 w-4" />, permission: 'view_gr' },
      { label: 'Invoices', href: '/invoices', icon: <FileText className="h-4 w-4" />, permission: 'view_invoice' },
      { label: 'Vendor Credits', href: '/vendor-credits', icon: <FileText className="h-4 w-4" />, permission: 'view_invoice' },
      { label: 'Payment Applications', href: '/pa', icon: <CreditCard className="h-4 w-4" />, permission: 'view_pa' },
      // Freight: registrars (buyers who ship parcels) and the supply-chain
      // people who match carrier invoices. Same keys freight.py's /freight/me checks.
      { label: 'Freight', href: '/freight', icon: <Truck className="h-4 w-4" />, anyPermission: ['epms.freight.register', 'epms.freight.manage', 'epms.freight.admin'] },
      { label: 'Agreements', href: '/agreements', icon: <FileSignature className="h-4 w-4" />, permission: 'epms.agreement.read' },
      // epms.agreement.read (read), NOT epms.agreement.receipt.write — anyone
      // who can see an agreement should be able to see its receipts too,
      // otherwise a read-only role (auditor, finance_bp, etc.) can reach
      // /agreements but never finds this entry in the sidebar at all.
      { label: 'Agreement Receipts', href: '/receipts', icon: <Receipt className="h-4 w-4" />, permission: 'epms.agreement.read' },
    ],
  },
  // NOTE: FINANCE section (Budget Dashboard / Budget Plans / Account Catalog)
  // moved to UniOps Portal sidebar — they are cross-module pages, not EPMS-specific.
  // The /budget* routes still exist here; Portal links navigate to them with
  // session handoff (#__session=...).
  {
    title: 'MASTER DATA',
    items: [
      {
        label: 'Vendors',
        href: '/vendors',
        icon: <Building2 className="h-4 w-4" />,
        permission: 'vendor_master',
      },
      {
        label: 'Projects',
        href: '/projects',
        icon: <FolderTree className="h-4 w-4" />,
        roles: ['procurement_officer', 'procurement_manager', 'system_admin'],
      },
      {
        label: 'Parts Catalog',
        href: '/parts',
        icon: <Wrench className="h-4 w-4" />,
        permission: 'parts_catalog',
      },
    ],
  },
  {
    title: 'ADMIN',
    items: [
      {
        label: 'Admin Panel',
        href: '/admin',
        icon: <Settings className="h-4 w-4" />,
        permission: 'admin_panel',
      },
    ],
  },
]

/** "MY WORKSPACE" -> "My workspace": section labels in sentence case. */
function sentence(title: string) {
  const t = title.toLowerCase()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

interface SidebarProps {
  mobileOpen?: boolean
  onMobileClose?: () => void
}

export function Sidebar({ mobileOpen = false, onMobileClose }: SidebarProps) {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed('epms.sidebar.collapsed')
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const perms = useRolePermissions().data?.permissions

  const isItemVisible = (item: NavItemDef) => {
    if (!user) return false
    if (item.permission) {
      return user.role === 'system_admin' || !!perms?.[item.permission]
    }
    if (item.anyPermission) {
      return user.role === 'system_admin' || item.anyPermission.some((k) => !!perms?.[k])
    }
    if (item.roles) return item.roles.includes(user.role)
    return true
  }

  const sections = NAV_SECTIONS
    .map((section) => ({
      title: sentence(section.title),
      items: section.items.filter(isItemVisible).map(({ label, href, icon, badge }) => ({ label, href, icon, badge })),
    }))
    .filter((section) => section.items.length > 0)

  return (
    <ShellSidebar
      brand={{
        name: config?.name ?? 'EPMS',
        tagline: config?.module_taglines?.epms ?? config?.tagline ?? '',
        logoUrl: config?.logo_data_url ?? null,
      }}
      sections={sections}
      collapsed={collapsed}
      onToggleCollapse={toggleCollapsed}
      mobileOpen={mobileOpen}
      onMobileClose={onMobileClose}
      portalUrl={PORTAL_URL}
    />
  )
}
