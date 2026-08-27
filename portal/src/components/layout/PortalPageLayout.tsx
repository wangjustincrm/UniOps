/**
 * PortalPageLayout — the standard Portal page shell: PortalSidebar + the real
 * TopHeader (search box + user menu — same component PortalHome uses, see
 * components/layout/TopHeader.tsx) + <main>. Every Portal page reached via the
 * sidebar (currently the /admin/* pages) must render its content through this
 * component instead of a bare <div> — that is exactly how AccessControl and
 * ApprovalRouting lost their sidebar (copied from DataMaintenance, which was
 * never wrapped either; see feedback_uniops_portal_page_chrome).
 *
 * session / perms / userRole / per-module hrefs and the sidebar's
 * mobileOpen / collapsed state all live here so pages don't have to know about
 * them — mirrors how PortalHome.tsx builds and passes the same values into
 * <PortalSidebar> and <TopHeader>. A page only supplies `activeKey` (which
 * sidebar item to highlight — see navConfig.tsx); TopHeader has no title slot
 * (mirrors PortalHome, which puts its page title in the content area, not the
 * header), so each page renders its own heading in `children` instead.
 */
import { useState, type ReactNode } from 'react'
import { useAuthStore } from '@/store/auth'
import { EPMS_URL, OA_URL, VMS_URL, FINANCE_URL, BOOKING_URL, encodeSession } from '@/lib/api'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { PortalSidebar } from './PortalSidebar'
import { TopHeader } from './TopHeader'

interface PortalPageLayoutProps {
  /** Sidebar item to highlight — must match a PORTAL_NAV_SECTIONS href, e.g. 'portal:/admin/access-control'. */
  activeKey: string
  children: ReactNode
}

export function PortalPageLayout({ activeKey, children }: PortalPageLayoutProps) {
  const auth = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const session = auth.token && auth.user
    ? encodeSession(auth.token, auth.refreshToken ?? '', auth.user)
    : ''

  const epmsHref = session ? `${EPMS_URL}/dashboard#__session=${session}` : EPMS_URL
  const oaHref   = session ? `${OA_URL}/pa#__session=${session}`           : OA_URL
  const vmsHref  = session ? `${VMS_URL}/#__session=${session}`            : VMS_URL
  // Land on Finance root — matches PortalHome (the Finance app redirects to the
  // first page the user can access rather than deep-linking to a gated route).
  const financeHref = session ? `${FINANCE_URL}/#__session=${session}` : FINANCE_URL
  const bookingHref = session ? `${BOOKING_URL}/#__session=${session}` : BOOKING_URL

  return (
    <div className="flex h-screen overflow-hidden bg-[#F5F6FA]">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/50 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* ── Sidebar ─────────────────────────────── */}
      <PortalSidebar
        activeKey={activeKey}
        epmsHref={epmsHref} oaHref={oaHref} vmsHref={vmsHref} financeHref={financeHref} bookingHref={bookingHref} session={session}
        userRole={auth.user?.role ?? null} perms={perms}
        mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)}
        collapsed={collapsed} onToggleCollapse={() => setCollapsed(v => !v)}
      />

      {/* ── Main area ───────────────────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden min-w-0">
        <TopHeader session={session} onMobileMenuToggle={() => setMobileOpen((v) => !v)} />

        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
