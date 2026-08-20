/**
 * TopHeader — the standard Portal header bar: mobile hamburger + search box +
 * user menu (Profile / Change Password / Sign Out). Originally defined inline
 * in PortalHome.tsx; extracted here so every Portal page (PortalHome and the
 * /admin/* pages via PortalPageLayout) renders the exact same header instead of
 * a slimmed-down copy. See feedback_uniops_portal_page_chrome.
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { LogOut, ChevronDown, User, KeyRound, Menu, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { globalSignOut } from '@/lib/signOut'
import { cn } from '@/lib/utils'
import { ChangePasswordForm } from '@/components/auth/ChangePasswordForm'

// ── Change Password modal ───────────────────────────────────────────────────
// Voluntary change from the user menu. The form itself is shared with the
// forced-rotation gate (ForcePasswordChangePage) — see components/auth.

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const { user } = useAuthStore()

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <KeyRound className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Change Password</h2>
              {user && <p className="text-xs text-neutral-500">{user.full_name} · {user.email}</p>}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-5 flex flex-col gap-4">
          <ChangePasswordForm onSuccess={onClose} onCancel={onClose} />
        </div>
      </div>
    </div>,
    document.body,
  )
}

// ── Top header ────────────────────────────────────────────────────────────────

export function TopHeader({ epmsHref, onMobileMenuToggle }: { epmsHref: string; onMobileMenuToggle: () => void }) {
  const { user } = useAuthStore()
  const [menuOpen, setMenuOpen] = useState(false)
  const [showChangePwd, setShowChangePwd] = useState(false)
  const initials = user?.full_name?.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase() ?? 'U'
  const roleLabel = user?.role?.replace(/_/g, ' ') ?? ''
  const [base, hash] = epmsHref.split('#')
  const profileHref = `${base}/profile${hash ? '#' + hash : ''}`

  return (
    <>
    <header className="flex h-[60px] shrink-0 items-center gap-4 border-b border-neutral-200 bg-white px-4 md:px-6">
      {/* Mobile hamburger */}
      <button
        className="rounded-md p-2 text-neutral-500 hover:bg-neutral-50 md:hidden"
        aria-label="Open menu"
        onClick={onMobileMenuToggle}
      >
        <Menu className="h-5 w-5" />
      </button>
      {/* Search */}
      <div className="flex flex-1 items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 max-w-xs">
        <svg className="h-4 w-4 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <span className="text-sm text-neutral-400">Search resources…</span>
      </div>

      {/* User menu */}
      <div className="ml-auto relative">
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-100 text-xs font-semibold text-primary-700 ring-2 ring-primary-400">
            {initials}
          </div>
          <span className="font-medium">{user?.full_name ?? 'User'}</span>
          <ChevronDown className={cn('h-3.5 w-3.5 transition-transform text-neutral-400', menuOpen && 'rotate-180')} />
        </button>

        {menuOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} aria-hidden="true" />
            <div className="absolute right-0 top-full z-20 mt-1 w-52 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
              {/* Identity */}
              <div className="border-b border-neutral-100 px-3 py-2">
                <p className="text-sm font-medium text-neutral-900">{user?.full_name}</p>
                <p className="text-xs text-neutral-500 capitalize">{roleLabel}</p>
              </div>
              {/* Profile → EPMS */}
              <a
                href={profileHref}
                onClick={() => setMenuOpen(false)}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
              >
                <User className="h-4 w-4" />
                Profile
              </a>
              {/* Change Password — opens in place, no cross-app navigation */}
              <button
                onClick={() => { setShowChangePwd(true); setMenuOpen(false) }}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
              >
                <KeyRound className="h-4 w-4" />
                Change Password
              </button>
              {/* Sign Out */}
              <div className="mt-1 border-t border-neutral-100">
                <button
                  onClick={() => { setMenuOpen(false); globalSignOut() }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
                >
                  <LogOut className="h-4 w-4" />
                  Sign Out
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </header>
    {showChangePwd && <ChangePasswordModal onClose={() => setShowChangePwd(false)} />}
    </>
  )
}
