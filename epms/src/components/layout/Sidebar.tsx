import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
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
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
} from 'lucide-react'
import { cn } from '@/lib/utils'
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

// Derive initials from a company name (up to 2 chars)
function nameInitials(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

interface SidebarProps {
  mobileOpen?: boolean
  onMobileClose?: () => void
}

export function Sidebar({ mobileOpen = false, onMobileClose }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false)
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const companyName = config?.name ?? 'EPMS'
  const companyTagline = config?.module_taglines?.epms ?? config?.tagline ?? ''
  const companyLogoUrl = config?.logo_data_url ?? null
  const location = useLocation()

  const perms = useRolePermissions().data?.permissions

  const isItemVisible = (item: NavItemDef) => {
    if (!user) return false
    if (item.permission) {
      return user.role === 'system_admin' || !!perms?.[item.permission]
    }
    if (item.roles) return item.roles.includes(user.role)
    return true
  }

  const isSectionVisible = (section: NavSection) =>
    section.items.some(isItemVisible)

  const handleNavClick = () => {
    onMobileClose?.()
  }

  return (
    <aside
      className={cn(
        'flex h-screen flex-col bg-primary-700 transition-all duration-200',
        // Desktop: fixed width, always visible
        collapsed ? 'w-16' : 'w-60',
        // Mobile: absolute overlay, hidden by default, slides in when open
        'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-30',
        mobileOpen ? 'max-md:translate-x-0 max-md:opacity-100' : 'max-md:-translate-x-full max-md:opacity-0',
        'max-md:w-60 max-md:shadow-2xl'
      )}
    >
      {/* Brand area */}
      <div className="flex h-[60px] items-center justify-between border-b border-white/10 px-3 gap-2">
        {/* Logo + name — expanded */}
        {!collapsed && (
          <div className="flex items-center gap-2.5 min-w-0">
            {/* Logo image or initials badge */}
            {companyLogoUrl ? (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white overflow-hidden">
                <img
                  src={companyLogoUrl}
                  alt={companyName}
                  className="h-full w-full object-contain p-0.5"
                />
              </div>
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/15">
                <span className="text-xs font-bold text-white">{nameInitials(companyName)}</span>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-sm font-bold text-white/95 leading-tight truncate">{companyName}</p>
              {companyTagline && (
                <p className="text-[10px] text-white/45 leading-tight truncate">{companyTagline}</p>
              )}
            </div>
          </div>
        )}

        {/* Collapsed — icon only */}
        {collapsed && (
          <div className="mx-auto">
            {companyLogoUrl ? (
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-white overflow-hidden">
                <img
                  src={companyLogoUrl}
                  alt={companyName}
                  className="h-full w-full object-contain p-0.5"
                />
              </div>
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-white/15">
                <span className="text-xs font-bold text-white">{nameInitials(companyName)}</span>
              </div>
            )}
          </div>
        )}

        {/* Collapse toggle */}
        {!collapsed && (
          <button
            onClick={() => setCollapsed(true)}
            className="shrink-0 rounded p-1 text-white/50 hover:bg-white/10 hover:text-white/90 transition-colors"
            aria-label="Collapse sidebar"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        {NAV_SECTIONS.map((section) => {
          if (!isSectionVisible(section)) return null
          return (
            <div key={section.title} className="mb-4">
              {!collapsed && (
                <p className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                  {section.title}
                </p>
              )}
              {collapsed && <div className="my-2 border-t border-white/10" />}
              {section.items.filter(isItemVisible).map((item) => (
                <NavLink
                  key={item.href}
                  to={item.href}
                  title={collapsed ? item.label : undefined}
                  onClick={handleNavClick}
                  className={({ isActive }) =>
                    cn(
                      'group relative flex items-center gap-2.5 rounded-md px-2 py-2.5 text-sm transition-colors min-h-[44px]',
                      isActive || location.pathname.startsWith(item.href + '/')
                        ? 'bg-white/15 text-white font-medium'
                        : 'text-white/70 hover:bg-white/10 hover:text-white/95',
                      collapsed && 'justify-center px-0'
                    )
                  }
                >
                  <span className="shrink-0">{item.icon}</span>
                  {!collapsed && <span className="truncate">{item.label}</span>}
                  {!collapsed && item.badge ? (
                    <span className="ml-auto flex h-5 min-w-5 items-center justify-center rounded-full bg-danger-600 px-1 text-[10px] font-bold text-white">
                      {item.badge > 99 ? '99+' : item.badge}
                    </span>
                  ) : null}
                  {collapsed && item.badge ? (
                    <span className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger-600 text-[9px] font-bold text-white">
                      {item.badge > 99 ? '99+' : item.badge}
                    </span>
                  ) : null}
                </NavLink>
              ))}
            </div>
          )
        })}
      </nav>

      {/* Back to Portal */}
      <div className="border-t border-white/10 p-2">
        <a
          href={(import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'}
          className={cn(
            'flex items-center gap-2 rounded-md px-2 py-2 text-sm text-white/60 hover:bg-white/10 hover:text-white/90 transition-colors',
            collapsed && 'justify-center px-0'
          )}
          title="Back to UniOps Portal"
        >
          <ArrowLeft className="h-4 w-4 shrink-0" />
          {!collapsed && <span>Back to Portal</span>}
        </a>
      </div>

      {/* Expand button when collapsed */}
      {collapsed && (
        <div className="border-t border-white/10 p-2">
          <button
            onClick={() => setCollapsed(false)}
            className="flex w-full items-center justify-center rounded p-2 text-white/50 hover:bg-white/10 hover:text-white/90 transition-colors"
            aria-label="Expand sidebar"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </aside>
  )
}
