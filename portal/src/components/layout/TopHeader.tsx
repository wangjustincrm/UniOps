/**
 * TopHeader — the standard Portal header bar: mobile hamburger + search box +
 * user menu (Profile / Change Password / Sign Out). Originally defined inline
 * in PortalHome.tsx; extracted here so every Portal page (PortalHome and the
 * /admin/* pages via PortalPageLayout) renders the exact same header instead of
 * a slimmed-down copy. See feedback_uniops_portal_page_chrome.
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import {
  LogOut, ChevronDown, User, KeyRound, Menu,
  CheckCircle2, X, Eye, EyeOff,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { epmsApi } from '@/lib/api'
import { globalSignOut } from '@/lib/signOut'
import { cn } from '@/lib/utils'

// ── Change Password modal ───────────────────────────────────────────────────
// Self-contained so the Portal can change passwords in place. Hits the same
// EPMS-API endpoint the EPMS header uses (`${EPMS_API}/api/v1/auth/change-password`)
// with the stored bearer token — no cross-app navigation.

function PwdField({
  label, value, onChange, show, onToggle, error, placeholder, autoFocus = false,
}: {
  label: string; value: string; onChange: (v: string) => void
  show: boolean; onToggle: () => void; error?: string; placeholder: string; autoFocus?: boolean
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium text-neutral-700">{label} <span className="text-red-500">*</span></label>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            'h-10 w-full rounded-lg border bg-white px-3 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-colors',
            error ? 'border-red-500' : 'border-neutral-300',
          )}
          placeholder={placeholder}
          autoFocus={autoFocus}
        />
        <button type="button" onClick={onToggle}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
          {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )
}

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const { user } = useAuthStore()
  const [currentPwd, setCurrentPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState(false)

  const clearErr = (key: string) => setErrors((p) => ({ ...p, [key]: '' }))

  const validate = (): boolean => {
    const e: Record<string, string> = {}
    if (!currentPwd) e.currentPwd = 'Required'
    if (!newPwd) e.newPwd = 'Required'
    else if (newPwd.length < 8) e.newPwd = 'Must be at least 8 characters'
    else if (newPwd === currentPwd) e.newPwd = 'New password must differ from current password'
    if (!confirmPwd) e.confirmPwd = 'Required'
    else if (confirmPwd !== newPwd) e.confirmPwd = 'Passwords do not match'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return
    setSubmitting(true)
    try {
      await epmsApi.post('/auth/change-password', { current_password: currentPwd, new_password: newPwd })
      setSuccess(true)
      setTimeout(onClose, 1500)
    } catch (err) {
      setErrors((e) => ({ ...e, currentPwd: err instanceof Error ? err.message : 'Current password is incorrect' }))
    } finally {
      setSubmitting(false)
    }
  }

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
          {success ? (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-green-50">
                <CheckCircle2 className="h-6 w-6 text-green-600" />
              </div>
              <p className="text-sm font-medium text-neutral-900">Password changed successfully</p>
            </div>
          ) : (
            <>
              <PwdField label="Current Password" value={currentPwd}
                onChange={(v) => { setCurrentPwd(v); clearErr('currentPwd') }}
                show={showCurrent} onToggle={() => setShowCurrent((v) => !v)}
                error={errors.currentPwd} placeholder="Enter current password" autoFocus />
              <PwdField label="New Password" value={newPwd}
                onChange={(v) => { setNewPwd(v); clearErr('newPwd') }}
                show={showNew} onToggle={() => setShowNew((v) => !v)}
                error={errors.newPwd} placeholder="Min. 8 characters" />
              <PwdField label="Confirm New Password" value={confirmPwd}
                onChange={(v) => { setConfirmPwd(v); clearErr('confirmPwd') }}
                show={showConfirm} onToggle={() => setShowConfirm((v) => !v)}
                error={errors.confirmPwd} placeholder="Re-enter new password" />
              <div className="flex justify-end gap-2 pt-1">
                <button onClick={onClose}
                  className="rounded-lg px-3 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-100">
                  Cancel
                </button>
                <button onClick={handleSubmit} disabled={submitting}
                  className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50">
                  <KeyRound className="h-3.5 w-3.5" />
                  {submitting ? 'Saving…' : 'Change Password'}
                </button>
              </div>
            </>
          )}
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
