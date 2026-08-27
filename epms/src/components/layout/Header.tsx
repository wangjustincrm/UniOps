import { useLocation, Link, useNavigate } from 'react-router-dom'
import { Bell, Globe, ChevronDown, User, Settings, LogOut, KeyRound, Eye, EyeOff, X, Mail, ShieldCheck, ShieldOff, AlertTriangle, Menu } from 'lucide-react'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { taskService } from '@/services/tasks'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth.store'
import { api } from '@/lib/api'
import { globalSignOut } from '@/lib/signOut'
import { authService } from '@/services/auth'
import { Button } from '@/components/ui/button'

const BREADCRUMB_LABELS: Record<string, string> = {
  dashboard: 'Dashboard',
  tasks: 'Task Inbox',
  pr: 'Purchase Requisitions',
  po: 'Purchase Orders',
  new: 'New',
  gr: 'Goods Receipt',
  invoices: 'Invoices',
  'vendor-credits': 'Vendor Credits',
  pa: 'Payment Applications',
  budget: 'Budget',
  reports: 'Reports',
  profile: 'My Profile',
  vendors: 'Vendors',
  projects: 'Projects',
  parts: 'Parts Catalog',
  admin: 'Admin Panel',
  create: 'Create',
  edit: 'Edit',
  collect: 'Collection',
  report: 'Receiving Report',
  settle: 'Settlement',
}

const DETAIL_LABELS: Record<string, string> = {
  pr: 'PR Detail',
  po: 'PO Detail',
  gr: 'GR Detail',
  invoices: 'Invoice Detail',
  pa: 'PA Detail',
  vendors: 'Vendor',
  projects: 'Project',
  parts: 'Part',
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const QUERY_KEY_MAP: Record<string, string> = {
  pr: 'pr', po: 'po', gr: 'gr', invoices: 'invoice', pa: 'pa',
  vendors: 'vendor', projects: 'project', parts: 'part',
}

function useBreadcrumbs() {
  const { pathname } = useLocation()
  const queryClient = useQueryClient()
  const parts = pathname.split('/').filter(Boolean)
  const crumbs = [{ label: 'Home', href: '/dashboard' }]
  let path = ''
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]
    path += `/${part}`
    if (path === '/dashboard') continue
    let label: string
    if (UUID_RE.test(part)) {
      const parentSegment = parts[i - 1]
      const queryKey = QUERY_KEY_MAP[parentSegment]
      const cached = queryKey
        ? (queryClient.getQueryData([queryKey, part]) as { number?: string } | undefined)
        : undefined
      label = cached?.number ?? (DETAIL_LABELS[parentSegment] ?? 'Detail')
    } else {
      label = BREADCRUMB_LABELS[part] ?? part
    }
    crumbs.push({ label, href: path })
  }
  return crumbs
}

// ─── Change Password Modal ────────────────────────────────────────────────────

interface ChangePasswordModalProps {
  forced?: boolean
  forceReason?: string
  onClose: () => void
}

function ChangePasswordModal({ forced = false, forceReason, onClose }: Omit<ChangePasswordModalProps, 'userId'>) {
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
      await api.post('/auth/change-password', { current_password: currentPwd, new_password: newPwd })
      setSuccess(true)
      setTimeout(() => onClose(), 1500)
    } catch (err) {
      setErrors((e) => ({ ...e, currentPwd: err instanceof Error ? err.message : 'Current password is incorrect' }))
    } finally {
      setSubmitting(false)
    }
  }

  const inputCls = (err?: string) => cn(
    'h-10 w-full rounded-lg border bg-white px-3 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 transition-colors',
    err ? 'border-danger-600' : 'border-neutral-300'
  )

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4 animate-[overlay-in_0.15s_ease-out]">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl animate-[modal-in_0.18s_ease-out]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <KeyRound className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Change Password</h2>
              {user && <p className="text-xs text-neutral-500">{user.name} · {user.email}</p>}

            </div>
          </div>
          {!forced && (
            <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="px-6 py-5 flex flex-col gap-4">
          {/* Forced reason banner */}
          {forced && forceReason && (
            <div className="flex items-start gap-2.5 rounded-lg bg-warning-50 border border-warning-200 px-4 py-3">
              <AlertTriangle className="h-4 w-4 shrink-0 text-warning-600 mt-0.5" />
              <p className="text-xs text-warning-700">{forceReason}</p>
            </div>
          )}

          {success ? (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-success-50">
                <KeyRound className="h-6 w-6 text-success-600" />
              </div>
              <p className="text-sm font-medium text-neutral-900">Password changed successfully</p>
              <p className="text-xs text-neutral-500">Redirecting…</p>
            </div>
          ) : (
            <>
              {/* Current password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-neutral-700">
                  Current Password <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <input
                    type={showCurrent ? 'text' : 'password'}
                    value={currentPwd}
                    onChange={(e) => { setCurrentPwd(e.target.value); setErrors((p) => ({ ...p, currentPwd: '' })) }}
                    className={inputCls(errors.currentPwd)}
                    placeholder="Enter current password"
                    autoFocus
                  />
                  <button type="button" onClick={() => setShowCurrent((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                    {showCurrent ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                {errors.currentPwd && <p className="text-xs text-danger-600">{errors.currentPwd}</p>}
              </div>

              {/* New password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-neutral-700">
                  New Password <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <input
                    type={showNew ? 'text' : 'password'}
                    value={newPwd}
                    onChange={(e) => { setNewPwd(e.target.value); setErrors((p) => ({ ...p, newPwd: '' })) }}
                    className={inputCls(errors.newPwd)}
                    placeholder="Min. 8 characters"
                  />
                  <button type="button" onClick={() => setShowNew((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                    {showNew ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                {errors.newPwd && <p className="text-xs text-danger-600">{errors.newPwd}</p>}
              </div>

              {/* Confirm password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-neutral-700">
                  Confirm New Password <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <input
                    type={showConfirm ? 'text' : 'password'}
                    value={confirmPwd}
                    onChange={(e) => { setConfirmPwd(e.target.value); setErrors((p) => ({ ...p, confirmPwd: '' })) }}
                    className={inputCls(errors.confirmPwd)}
                    placeholder="Re-enter new password"
                  />
                  <button type="button" onClick={() => setShowConfirm((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                    {showConfirm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                {errors.confirmPwd && <p className="text-xs text-danger-600">{errors.confirmPwd}</p>}
              </div>

              <div className="flex justify-end gap-2 pt-1">
                {!forced && (
                  <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
                )}
                <Button size="sm" onClick={handleSubmit} disabled={submitting}>
                  <KeyRound className="h-3.5 w-3.5" />
                  {submitting ? 'Saving…' : 'Change Password'}
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Profile Modal ────────────────────────────────────────────────────────────

function ProfileModal({ onClose }: { onClose: () => void }) {
  const { user } = useAuthStore()
  const [mfaLoading, setMfaLoading] = useState(false)
  const [mfaMsg, setMfaMsg] = useState('')

  // Read live mfa_enabled from users list isn't available here; use a simple local toggle
  const [mfaEnabled, setMfaEnabled] = useState<boolean | null>(null)

  // Fetch current user's mfa status on open
  useState(() => {
    authService.me().then((u) => setMfaEnabled(u.mfa_enabled)).catch(() => {})
  })

  const toggleMfa = async () => {
    if (mfaEnabled === null) return
    setMfaLoading(true); setMfaMsg('')
    try {
      if (mfaEnabled) {
        await authService.mfaDisable()
        setMfaEnabled(false)
        setMfaMsg('Email MFA disabled.')
      } else {
        await authService.mfaEnable()
        setMfaEnabled(true)
        setMfaMsg('Email MFA enabled. A code will be sent to your email at next login.')
      }
    } catch (e) {
      setMfaMsg(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setMfaLoading(false)
    }
  }

  const ROLE_LABELS: Record<string, string> = {
    system_admin: 'System Admin', dept_manager: 'Department Manager',
    gm: 'General Manager', opm: 'Operations Manager',
    procurement_officer: 'Procurement Officer', procurement_manager: 'Procurement Manager',
    ap_clerk: 'AP Clerk', finance_bp: 'Finance Business Partner',
    finance_manager: 'Finance Manager', cfo: 'CFO',
    requester: 'Requester', auditor: 'Auditor',
    vendor_manager: 'Vendor Manager', warehouse: 'Warehouse',
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4 animate-[overlay-in_0.15s_ease-out]">
      <div className="w-full max-w-sm rounded-2xl bg-white shadow-2xl animate-[modal-in_0.18s_ease-out]">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <User className="h-5 w-5 text-primary-600" />
            </div>
            <h2 className="text-sm font-semibold text-neutral-900">My Profile</h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-5 flex flex-col gap-4">
          {/* Avatar + name */}
          <div className="flex items-center gap-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary-100 text-lg font-bold text-primary-700">
              {user?.name.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2)}
            </div>
            <div>
              <p className="text-base font-semibold text-neutral-900">{user?.name}</p>
              <p className="text-xs text-neutral-500">{ROLE_LABELS[user?.role ?? ''] ?? user?.role}</p>
            </div>
          </div>

          {/* Info rows */}
          <div className="rounded-xl border border-neutral-100 bg-neutral-50 divide-y divide-neutral-100">
            <div className="flex items-center gap-3 px-4 py-3">
              <Mail className="h-4 w-4 text-neutral-400 shrink-0" />
              <div>
                <p className="text-xs text-neutral-500">Email</p>
                <p className="text-sm text-neutral-900">{user?.email}</p>
              </div>
            </div>
            <div className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-3">
                {mfaEnabled ? <ShieldCheck className="h-4 w-4 text-success-600 shrink-0" /> : <ShieldOff className="h-4 w-4 text-neutral-400 shrink-0" />}
                <div>
                  <p className="text-xs text-neutral-500">Email MFA</p>
                  <p className="text-sm text-neutral-900">{mfaEnabled === null ? 'Loading…' : mfaEnabled ? 'Enabled' : 'Disabled'}</p>
                </div>
              </div>
              <button
                onClick={toggleMfa}
                disabled={mfaLoading || mfaEnabled === null}
                className={cn(
                  'relative h-5 w-9 shrink-0 rounded-full transition-colors focus:outline-none disabled:opacity-50',
                  mfaEnabled ? 'bg-primary-600' : 'bg-neutral-300'
                )}
              >
                <span className={cn('absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', mfaEnabled ? 'translate-x-4' : 'translate-x-0')} />
              </button>
            </div>
          </div>

          {mfaMsg && <p className="text-xs text-neutral-600 bg-neutral-50 rounded-lg px-3 py-2">{mfaMsg}</p>}

          <div className="flex justify-end">
            <Button variant="secondary" size="sm" onClick={onClose}>Close</Button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Forced Change Password Modal (first-login / after admin reset) ──────────

export function ForcedChangePasswordModal({ onDone }: { onDone: () => void }) {
  return (
    <ChangePasswordModal
      forced
      forceReason="Your password was set by an administrator. You must choose a new password before continuing."
      onClose={onDone}
    />
  )
}

// ─── Header ───────────────────────────────────────────────────────────────────

interface HeaderProps {
  onMobileMenuToggle?: () => void
}

export function Header({ onMobileMenuToggle }: HeaderProps) {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const breadcrumbs = useBreadcrumbs()
  const [menuOpen, setMenuOpen] = useState(false)
  const [showChangePwd, setShowChangePwd] = useState(false)
  const { data: taskData } = useQuery({
    queryKey: ['tasks', { is_completed: false }],
    queryFn: () => taskService.list({ is_completed: false }),
    refetchInterval: 60_000,
  })
  const notificationCount = taskData?.total ?? 0

  return (
    <>
      <header className="flex h-[60px] items-center justify-between bg-white px-4 md:px-6 shadow-[0_1px_3px_rgba(10,124,124,0.08)]" style={{ zIndex: 10, position: 'relative' }}>
        {/* Hamburger — mobile only */}
        <button
          className="mr-3 rounded-md p-2 text-neutral-500 hover:bg-neutral-50 md:hidden"
          aria-label="Open navigation menu"
          onClick={onMobileMenuToggle}
        >
          <Menu className="h-5 w-5" />
        </button>

        {/* Breadcrumb */}
        <nav aria-label="Breadcrumb">
          <ol className="flex items-center gap-1 text-sm">
            {breadcrumbs.map((crumb, i) => (
              <li key={crumb.href} className="flex items-center gap-1">
                {i > 0 && <span className="text-neutral-300">/</span>}
                {i === breadcrumbs.length - 1 ? (
                  <span className="font-medium text-neutral-900">{crumb.label}</span>
                ) : (
                  <Link to={crumb.href} className="text-neutral-500 hover:text-primary-600">
                    {crumb.label}
                  </Link>
                )}
              </li>
            ))}
          </ol>
        </nav>

        {/* Right controls */}
        <div className="flex items-center gap-3">
          {/* Notification bell */}
          <button
            onClick={() => navigate('/tasks')}
            className="relative rounded-md p-2 text-neutral-500 hover:bg-neutral-50 hover:text-neutral-700"
            aria-label={`${notificationCount} notifications`}
          >
            <Bell className="h-5 w-5" />
            {notificationCount > 0 && (
              <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger-600 px-0.5 text-[10px] font-bold text-white">
                {notificationCount > 99 ? '99+' : notificationCount}
              </span>
            )}
          </button>

          {/* Language toggle */}
          <button className="rounded-md px-2 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 flex items-center gap-1">
            <Globe className="h-3.5 w-3.5" />
            EN
          </button>

          {/* User menu */}
          <div className="relative">
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
              aria-haspopup="true"
              aria-expanded={menuOpen}
            >
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-100 text-xs font-semibold text-primary-700">
                {user?.name.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2)}
              </div>
              <span className="hidden sm:block font-medium">{user?.name}</span>
              <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', menuOpen && 'rotate-180')} />
            </button>

            {menuOpen && (
              <>
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setMenuOpen(false)}
                  aria-hidden="true"
                />
                <div className="absolute right-0 top-full z-20 mt-1 w-52 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
                  <div className="border-b border-neutral-100 px-3 py-2">
                    <p className="text-sm font-medium text-neutral-900">{user?.name}</p>
                    <p className="text-xs text-neutral-500 capitalize">{user?.role?.replace('_', ' ')}</p>
                  </div>
                  <button
                    onClick={() => { navigate('/profile'); setMenuOpen(false) }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
                  >
                    <User className="h-4 w-4" />
                    Profile
                  </button>
                  {user?.role === 'system_admin' && (
                    <button
                      onClick={() => { navigate('/admin'); setMenuOpen(false) }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
                    >
                      <Settings className="h-4 w-4" />
                      Settings
                    </button>
                  )}
                  <button
                    onClick={() => { setShowChangePwd(true); setMenuOpen(false) }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
                  >
                    <KeyRound className="h-4 w-4" />
                    Change Password
                  </button>
                  <div className="border-t border-neutral-100 mt-1">
                    <button
                      onClick={globalSignOut}
                      className="flex w-full items-center gap-2 px-3 py-2 text-sm text-danger-600 hover:bg-danger-50"
                    >
                      <LogOut className="h-4 w-4" />
                      Sign Out
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Change Password Modal */}
      {showChangePwd && (
        <ChangePasswordModal
          onClose={() => setShowChangePwd(false)}
        />
      )}
    </>
  )
}
