/**
 * TopHeader — the standard Portal header bar (Shell Topbar): mobile hamburger +
 * user menu (Profile / Change Password / Sign Out). Originally defined inline
 * in PortalHome.tsx; extracted here so every Portal page (PortalHome and the
 * /admin/* pages via PortalPageLayout) renders the exact same header instead of
 * a slimmed-down copy. See feedback_uniops_portal_page_chrome.
 */
import { useState } from 'react'
import { User, KeyRound } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { EPMS_URL } from '@/lib/api'
import { globalSignOut } from '@/lib/signOut'
import { ChangePasswordForm } from '@/components/auth/ChangePasswordForm'

import { Modal, Topbar, UserMenu } from '@uniops/shell'
// ── Change Password modal ───────────────────────────────────────────────────
// Voluntary change from the user menu. The form itself is shared with the
// forced-rotation gate (ForcePasswordChangePage) — see components/auth.

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const { user } = useAuthStore()
  // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
  return (
    <Modal open onClose={onClose} closeOnEscape={false} size="full" className="max-w-md"
      icon={<KeyRound className="h-4 w-4" />}
      title="Change Password"
      description={user ? `${user.full_name} · ${user.email}` : undefined}>
      <div className="flex flex-col gap-4">
        <ChangePasswordForm onSuccess={onClose} onCancel={onClose} />
      </div>
    </Modal>
  )
}

// ── Top header ────────────────────────────────────────────────────────────────

export function TopHeader({ session, onMobileMenuToggle }: { session: string; onMobileMenuToggle: () => void }) {
  const { user } = useAuthStore()
  const [showChangePwd, setShowChangePwd] = useState(false)
  const roleLabel = (user?.role ?? '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  // EPMS owns the profile page. Built from EPMS_URL directly rather than by
  // appending to the module href: that href already carries a landing path
  // (`${EPMS_URL}/dashboard#…`), so appending produced `/dashboard/profile`,
  // which matches no EPMS route — the tab shell fell back to Dashboard and the
  // menu item looked like it just opened EPMS.
  const profileHref = session
    ? `${EPMS_URL}/profile#__session=${session}`
    : `${EPMS_URL}/profile`

  return (
    <>
      <Topbar
        onMobileMenu={onMobileMenuToggle}
        className="relative z-10"
        end={
          <UserMenu
            name={user?.full_name ?? 'User'}
            subtitle={roleLabel}
            items={[
              { label: 'Profile', icon: <User />, href: profileHref },
              { label: 'Change Password', icon: <KeyRound />, onSelect: () => setShowChangePwd(true) },
            ]}
            onSignOut={globalSignOut}
          />
        }
      />
      {showChangePwd && <ChangePasswordModal onClose={() => setShowChangePwd(false)} />}
    </>
  )
}
