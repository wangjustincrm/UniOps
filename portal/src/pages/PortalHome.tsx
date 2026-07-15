import { useState, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import {
  UserCheck, LogOut, ChevronDown, User, KeyRound, Menu,
  ArrowRight, CheckCircle2, AlertCircle,
  Briefcase, CreditCard, Activity, Cloud, Landmark, CalendarClock,
  X, Eye, EyeOff,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { epmsApi, oaApi, EPMS_URL, OA_URL, VMS_URL, FINANCE_URL, BOOKING_URL, encodeSession } from '@/lib/api'
import { globalSignOut } from '@/lib/signOut'
import { cn, formatAmount, timeAgo } from '@/lib/utils'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { PortalSidebar } from '@/components/layout/PortalSidebar'
import { FINANCE_ACCESS_PERMS, BOOKING_ACCESS_PERMS } from '@/components/layout/navConfig'

// ── Config ────────────────────────────────────────────────────────────────────

const EPMS_API = (import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'
const OA_API   = (import.meta.env.VITE_OA_API_URL   as string | undefined) || 'http://localhost:8006'

// ── Types ─────────────────────────────────────────────────────────────────────

interface EpmsTask {
  id: string
  type: string
  priority: string
  document_type: string
  document_id: string
  document_number: string
  title: string
  amount: number | null
  vendor: string | null
  created_at: string
}

interface OaTask {
  id: string
  claim_number: string
  claim_type: string
  employee_name: string
  net_amount: number
  currency: string
  status: string
  submitted_at: string | null
  created_at: string
}

interface UnifiedTask {
  id: string
  module: 'EPMS' | 'EXPENSE' | 'VMS'
  title: string
  docNumber: string
  /** Identity used to merge the same task echoed across feeds. Defaults to
   *  docNumber, but doc types with non-unique numbers (e.g. budget plans, whose
   *  number is the synthetic "BP-FY2026" shared by every cost center) must key on
   *  the document id instead so distinct rows don't collapse into one. */
  dedupKey: string
  amount: number | null
  currency: string
  urgent: boolean
  href: string
  createdAt: string
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useHealthCheck(apiBase: string, path: string, key: string) {
  return useQuery<{ ok: boolean; latencyMs: number }>({
    queryKey: [key, 'health-v2'],
    queryFn: async () => {
      const t0 = performance.now()
      const res = await fetch(`${apiBase}${path}`).catch(() => null)
      return { ok: !!res?.ok, latencyMs: Math.round(performance.now() - t0) }
    },
    retry: 0,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}

function useEpmsTasks() {
  return useQuery<{ items: EpmsTask[]; total: number }>({
    queryKey: ['portal-epms-tasks'],
    queryFn: () => epmsApi.get<{ items: EpmsTask[]; total: number }>('/tasks?is_completed=false'),
    staleTime: 60_000,
    retry: 1,
  })
}

function useOaTasks() {
  return useQuery<{ items: OaTask[]; total: number }>({
    queryKey: ['portal-oa-tasks'],
    queryFn: () => oaApi.get<{ items: OaTask[]; total: number }>('/api/v1/expenses/my-actions'),
    staleTime: 60_000,
    retry: 1,
  })
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildSession(auth: ReturnType<typeof useAuthStore.getState>): string {
  if (!auth.token || !auth.user) return ''
  return encodeSession(auth.token, auth.refreshToken ?? '', auth.user)
}

const DOC_PATH: Record<string, string> = {
  pr: '/pr', po: '/po', pa: '/pa', gr: '/gr', invoice: '/invoices',
  // budget_plan is handled separately — it deep-links into the Finance module
  // (see the budget_plan branch in the task mapper), not EPMS.
  // VMS doc types deep-link into the VMS frontend, not EPMS.
  // VMS_DEEPLINK below picks the right per-doc-type path.
  vms_visit: '/',
  vms_train: '/visitor',
  vms_ppe:   '/visitor',
}

// Tasks whose document_type belongs to VMS (deep-linked to VMS_URL, not EPMS_URL).
const VMS_DOC_TYPES = new Set(['vms_visit', 'vms_train', 'vms_ppe'])

// OA-owned doc types. Expense claims and Direct PAs reach approvers via approval-api
// and therefore surface in the EPMS task feed too — but they must deep-link into OA,
// not EPMS. Expense claims → /expenses/:id, Direct PAs → /pa/:id.
// NOTE: only "pa_dir" (Direct PA) is OA-owned. "pa" is a PO-based PA owned by EPMS
// (it has an EPMS PaDetailPage), so it must NOT be routed/labelled as OA/Expense.
function oaPathFor(docType: string): string | null {
  const dt = docType.toLowerCase()
  if (['exp', 'mil', 'trv', 'cfm'].includes(dt) || dt.startsWith('cfm')) return '/expenses'
  if (dt === 'pa_dir') return '/pa'
  return null
}

// Per-doc-type deeplink path under VMS_URL. document_id is the visit ID for
// vms_visit and the visitor ID for vms_train / vms_ppe.
function vmsDeeplinkPath(docType: string, docId: string): string {
  if (docType === 'vms_train' || docType === 'vms_ppe') {
    return `/visitor/${docId}/compliance`
  }
  return `/${docId}`
}

const CLAIM_TYPE_LABEL: Record<string, string> = {
  EXP: 'Expense Claim', MIL: 'Mileage Claim', TRV: 'Travel Expense',
}

const STATUS_LABEL: Record<string, string> = {
  submitted: 'Pending Dept. Approval',
  in_review: 'Pending Finance Review',
  approved:  'Approved — Awaiting Payment',
  pending_approval: 'Pending Visit Approval',  // shared by VMS
}

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

function TopHeader({ epmsHref, onMobileMenuToggle }: { epmsHref: string; onMobileMenuToggle: () => void }) {
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

// ── Module card ───────────────────────────────────────────────────────────────

interface ModuleCardProps {
  icon: React.ReactNode
  iconBg: string
  label: string
  description: string
  href: string
  healthy: boolean | undefined
  loading: boolean
  comingSoon?: boolean
}

function ModuleCard({ icon, iconBg, label, description, href, healthy, loading, comingSoon = false }: ModuleCardProps) {
  const isDown = !comingSoon && !loading && healthy === false
  const statusLabel = comingSoon ? 'Coming Soon' : isDown ? 'Offline' : 'Active'
  const statusColor = comingSoon
    ? 'bg-neutral-100 text-neutral-400'
    : isDown
      ? 'bg-red-50 text-red-500'
      : 'bg-green-50 text-green-600'

  const inner = (
    <div className={cn(
      'flex h-full flex-col rounded-xl border bg-white p-5 transition-all duration-150',
      comingSoon
        ? 'opacity-50 cursor-not-allowed border-neutral-200'
        : isDown
          ? 'opacity-60 cursor-not-allowed border-neutral-200'
          : 'cursor-pointer border-neutral-200 hover:border-primary-400 hover:shadow-teal-md group',
    )}>
      <div className="flex items-start justify-between mb-4">
        <div className={cn('flex h-10 w-10 items-center justify-center rounded-lg', iconBg)}>
          {icon}
        </div>
        <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide', statusColor)}>
          {statusLabel}
        </span>
      </div>
      <p className="text-sm font-bold text-neutral-900">{label}</p>
      <p className="mt-1 text-xs text-neutral-500 leading-relaxed">{description}</p>
      {!comingSoon && (
        <div className={cn(
          'mt-auto pt-4 flex items-center gap-1 text-xs font-medium transition-colors',
          isDown ? 'text-neutral-300' : 'text-primary-600 group-hover:text-primary-700',
        )}>
          Explore Module <ArrowRight className="h-3.5 w-3.5" />
        </div>
      )}
    </div>
  )

  return comingSoon || isDown
    ? <div className="h-full">{inner}</div>
    : <a href={href} className="block h-full">{inner}</a>
}

// ── Task row ──────────────────────────────────────────────────────────────────

const MODULE_STYLE: Record<string, { border: string; badge: string; label: string }> = {
  EPMS:    { border: 'border-l-primary-500',  badge: 'text-primary-600',  label: 'EPMS' },
  EXPENSE: { border: 'border-l-amber-400',    badge: 'text-amber-600',    label: 'Expense' },
  VMS:     { border: 'border-l-emerald-500',  badge: 'text-emerald-600',  label: 'Visitor' },
}

function TaskRow({ task }: { task: UnifiedTask }) {
  const style = MODULE_STYLE[task.module] ?? MODULE_STYLE.EPMS
  return (
    <a
      href={task.href}
      className={cn(
        'flex items-center gap-4 rounded-xl border border-neutral-200 border-l-4 bg-white px-4 py-3.5 hover:shadow-teal-sm hover:border-l-primary-600 transition-all group',
        style.border,
      )}
    >
      {/* Icon */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-neutral-50">
        {task.module === 'EPMS' && <Briefcase className="h-4 w-4 text-primary-500" />}
        {task.module === 'EXPENSE' && <CreditCard className="h-4 w-4 text-amber-500" />}
        {task.module === 'VMS' && <UserCheck className="h-4 w-4 text-emerald-500" />}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={cn('text-[10px] font-semibold uppercase tracking-wider', style.badge)}>
            {style.label}
          </span>
          <span className="text-[11px] text-neutral-400">{timeAgo(task.createdAt)}</span>
          {task.urgent && (
            <span className="rounded-full bg-red-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-red-500">Urgent</span>
          )}
        </div>
        <p className="text-sm font-medium text-neutral-800 truncate">{task.title}</p>
        <p className="mt-0.5 font-mono text-[11px] text-neutral-400">{task.docNumber}</p>
      </div>

      {/* Amount */}
      {task.amount != null && (
        <div className="shrink-0 text-right">
          <p className="text-sm font-mono font-semibold text-neutral-800">
            {formatAmount(task.amount, task.currency)}
          </p>
        </div>
      )}

      <ArrowRight className="h-4 w-4 shrink-0 text-neutral-300 group-hover:text-primary-500 transition-colors" />
    </a>
  )
}

// ── Platform Health widget ────────────────────────────────────────────────────

function PlatformHealth({
  epmsHealth, oaHealth,
}: {
  epmsHealth: { ok: boolean; latencyMs: number } | undefined
  oaHealth: { ok: boolean; latencyMs: number } | undefined
}) {
  const allOk = epmsHealth?.ok && oaHealth?.ok
  const onlineCount = [epmsHealth?.ok, oaHealth?.ok].filter(Boolean).length
  const avgLatency = epmsHealth && oaHealth
    ? Math.round((epmsHealth.latencyMs + oaHealth.latencyMs) / 2)
    : epmsHealth?.latencyMs ?? oaHealth?.latencyMs ?? 0

  const latencyPct = Math.min(100, Math.max(5, 100 - (avgLatency / 5)))
  const services = [
    { label: 'EPMS API',    ok: epmsHealth?.ok ?? null, ms: epmsHealth?.latencyMs },
    { label: 'Expense API', ok: oaHealth?.ok  ?? null, ms: oaHealth?.latencyMs   },
  ]

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
          <Activity className="h-4 w-4 text-neutral-400" />
          Platform Health
        </h3>
        {allOk
          ? <CheckCircle2 className="h-5 w-5 text-green-500" />
          : <AlertCircle className="h-5 w-5 text-amber-500" />
        }
      </div>

      {/* Uptime row */}
      <div className="mb-3">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-neutral-500">Services Online</span>
          <span className="font-semibold text-neutral-800">{onlineCount}/2</span>
        </div>
        <div className="h-1.5 w-full rounded-full bg-neutral-100">
          <div
            className={cn('h-1.5 rounded-full transition-all', onlineCount === 2 ? 'bg-primary-500' : onlineCount === 1 ? 'bg-amber-400' : 'bg-red-400')}
            style={{ width: `${(onlineCount / 2) * 100}%` }}
          />
        </div>
      </div>

      {/* Response time */}
      <div className="mb-4">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-neutral-500">Avg. Response Time</span>
          <span className="font-semibold text-neutral-800">{avgLatency}ms</span>
        </div>
        <div className="h-1.5 w-full rounded-full bg-neutral-100">
          <div
            className={cn(
              'h-1.5 rounded-full transition-all',
              avgLatency < 200 ? 'bg-green-500' : avgLatency < 500 ? 'bg-amber-400' : 'bg-red-400',
            )}
            style={{ width: `${latencyPct}%` }}
          />
        </div>
      </div>

      {/* Per-service status */}
      <div className="space-y-2 border-t border-neutral-100 pt-3">
        {services.map((svc) => (
          <div key={svc.label} className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <div className={cn(
                'h-1.5 w-1.5 rounded-full',
                svc.ok === null ? 'bg-neutral-300' : svc.ok ? 'bg-green-500' : 'bg-red-500',
              )} />
              <span className="text-neutral-600">{svc.label}</span>
            </div>
            <span className={cn('text-[11px]', svc.ok ? 'text-neutral-400' : 'text-red-500')}>
              {svc.ok === null ? '—' : svc.ok ? `${svc.ms}ms` : 'Offline'}
            </span>
          </div>
        ))}
      </div>

      {/* Status banner */}
      <div className={cn(
        'mt-3 flex items-center gap-2 rounded-lg px-3 py-2',
        allOk ? 'bg-green-50' : 'bg-amber-50',
      )}>
        <Cloud className={cn('h-4 w-4 shrink-0', allOk ? 'text-green-500' : 'text-amber-500')} />
        <p className={cn('text-xs font-medium', allOk ? 'text-green-700' : 'text-amber-700')}>
          {allOk ? 'All systems are operational.' : 'Some services are degraded.'}
        </p>
      </div>
    </div>
  )
}

// ── Recent Activity widget ────────────────────────────────────────────────────

function RecentActivity({ tasks }: { tasks: UnifiedTask[] }) {
  const recent = tasks.slice(0, 5)
  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <h3 className="mb-4 text-sm font-semibold text-neutral-800">Recent Activity</h3>
      {recent.length === 0 ? (
        <p className="text-xs text-neutral-400 text-center py-4">No recent activity</p>
      ) : (
        <div className="space-y-3">
          {recent.map((t) => (
            <a key={t.id} href={t.href} className="flex items-start gap-2.5 group">
              <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-green-500" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-neutral-800 leading-snug truncate group-hover:text-primary-600 transition-colors">
                  {t.title}
                </p>
                <p className="text-[11px] text-neutral-400 font-mono">{t.docNumber}</p>
              </div>
              <span className="shrink-0 text-[11px] text-neutral-400">{timeAgo(t.createdAt)}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Portal Home ───────────────────────────────────────────────────────────────

export default function PortalHome() {
  const auth = useAuthStore()
  const session = buildSession(auth)
  const perms = useRolePermissions().data?.permissions
  const role = auth.user?.role ?? null
  const hasFinanceAccess =
    role === 'system_admin' ||
    FINANCE_ACCESS_PERMS.some((p) => !!perms?.[p])
  const hasBookingAccess =
    role === 'system_admin' ||
    BOOKING_ACCESS_PERMS.some((p) => !!perms?.[p])
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const epmsHealth = useHealthCheck(EPMS_API, '/api/v1/health', 'epms')
  const oaHealth   = useHealthCheck(OA_API,   '/health',        'oa')
  const epmsTasks  = useEpmsTasks()
  const oaTasks    = useOaTasks()

  const epmsHref = session ? `${EPMS_URL}/dashboard#__session=${session}` : EPMS_URL
  const oaHref   = session ? `${OA_URL}/pa#__session=${session}`           : OA_URL
  const vmsHref  = session ? `${VMS_URL}/#__session=${session}`            : VMS_URL
  // Land on Finance root; the Finance app redirects to the first page the user
  // can access (don't deep-link to /finance/ap which needs view_finance).
  const financeHref = session ? `${FINANCE_URL}/#__session=${session}` : FINANCE_URL
  const bookingHref = session ? `${BOOKING_URL}/#__session=${session}` : BOOKING_URL

  const allTasks = useMemo<UnifiedTask[]>(() => {
    const withSession = (url: string) => (session ? `${url}#__session=${session}` : url)

    // OA expense/PA tasks own their links — list them first so dedup keeps the
    // OA-sourced row (nicer title) over the same task echoed by the EPMS feed.
    const oaRows = (oaTasks.data?.items ?? []).map((t): UnifiedTask => ({
      id: `oa-${t.id}`,
      module: 'EXPENSE',
      title: `${CLAIM_TYPE_LABEL[t.claim_type] ?? t.claim_type} — ${STATUS_LABEL[t.status] ?? t.status}`,
      docNumber: t.claim_number,
      dedupKey: t.claim_number,
      amount: t.net_amount,
      currency: t.currency,
      urgent: false,
      href: withSession(`${OA_URL}/expenses/${t.id}`),
      createdAt: t.submitted_at ?? t.created_at,
    }))

    const epmsRows = (epmsTasks.data?.items ?? []).map((t): UnifiedTask => {
      const isVms = VMS_DOC_TYPES.has(t.document_type)
      const oaPath = oaPathFor(t.document_type)
      let module: UnifiedTask['module'] = 'EPMS'
      let base = EPMS_URL
      let path: string
      if (isVms) {
        module = 'VMS'; base = VMS_URL
        path = vmsDeeplinkPath(t.document_type, t.document_id)
      } else if (oaPath) {
        // OA-owned doc surfaced via approval-api → deep-link into OA, not EPMS.
        module = 'EXPENSE'; base = OA_URL
        path = `${oaPath}/${t.document_id}`
      } else if (t.document_type.toLowerCase() === 'budget_plan') {
        // Budget is owned by the Finance module — deep-link into Finance (which
        // wraps the EPMS plan editor in Portal chrome), not bare EPMS.
        base = FINANCE_URL
        path = `/budget/plans/${t.document_id}`
      } else {
        path = `${DOC_PATH[t.document_type] ?? '/dashboard'}/${t.document_id}`
      }
      // Budget plans all share the synthetic number "BP-FY<year>", so dedup must
      // key on the plan id (document_id) — otherwise every cost center's plan
      // collapses into a single row. Other doc types keep numbering by docNumber
      // so an OA claim and its EPMS echo still merge.
      const dedupKey = t.document_type.toLowerCase() === 'budget_plan'
        ? `budget_plan:${t.document_id}`
        : t.document_number
      return {
        id: `epms-${t.id}`,
        module,
        title: t.title,
        docNumber: t.document_number,
        dedupKey,
        amount: t.amount,
        currency: 'CAD',
        urgent: t.priority === 'urgent',
        href: withSession(`${base}${path}`),
        createdAt: t.created_at,
      }
    })

    // Dedup: the same expense claim can appear in both feeds (OA my-actions +
    // approval-api via EPMS). Keep the first occurrence by dedup key.
    const seen = new Set<string>()
    return [...oaRows, ...epmsRows]
      .filter((t) => {
        const key = t.dedupKey || t.id
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
  }, [epmsTasks.data, oaTasks.data, session])

  const taskCount = allTasks.length
  const firstName = auth.user?.full_name?.split(' ')[0] ?? 'there'

  const isLoading = epmsTasks.isLoading || oaTasks.isLoading

  const MODULES: ModuleCardProps[] = [
    {
      icon: <Briefcase className="h-5 w-5 text-primary-600" />,
      iconBg: 'bg-primary-50',
      label: 'EPMS',
      description: 'Enterprise procurement, purchase orders, goods receipt, invoices',
      href: epmsHref,
      healthy: epmsHealth.data?.ok,
      loading: epmsHealth.isLoading,
    },
    {
      icon: <CreditCard className="h-5 w-5 text-amber-500" />,
      iconBg: 'bg-amber-50',
      label: 'OA',
      description: 'Reimbursements, vendor payments, mileage claims',
      href: oaHref,
      healthy: oaHealth.data?.ok,
      loading: oaHealth.isLoading,
    },
    ...(hasFinanceAccess ? [{
      icon: <Landmark className="h-5 w-5 text-primary-600" />,
      iconBg: 'bg-primary-50',
      label: 'Finance',
      description: 'AP/AR, general ledger, payments, bank rec, budgets, tax',
      href: financeHref,
      healthy: undefined,
      loading: false,
    }] : []),
    // Row break: with the 3-column grid, EPMS/OA/Finance fill row 1;
    // VMS + Meeting Rooms flow onto row 2.
    {
      icon: <UserCheck className="h-5 w-5 text-primary-600" />,
      iconBg: 'bg-primary-50',
      label: 'VMS',
      description: 'Visitor appointments, badge printing, on-site tracking',
      href: vmsHref,
      // No health check yet — vms-api healthcheck wiring lands in W7.
      healthy: undefined,
      loading: false,
    },
    ...(hasBookingAccess ? [{
      icon: <CalendarClock className="h-5 w-5 text-teal-600" />,
      iconBg: 'bg-teal-50',
      label: 'Meeting Rooms',
      description: 'Find and book meeting rooms',
      href: bookingHref,
      healthy: undefined,
      loading: false,
    }] : []),
  ]

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
        activeKey="portal:/"
        epmsHref={epmsHref} oaHref={oaHref} vmsHref={vmsHref} financeHref={financeHref} bookingHref={bookingHref} session={session}
        userRole={auth.user?.role ?? null} perms={perms}
        mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)}
        collapsed={collapsed} onToggleCollapse={() => setCollapsed(v => !v)}
      />

      {/* ── Main area ───────────────────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden min-w-0">
        <TopHeader epmsHref={epmsHref} onMobileMenuToggle={() => setMobileOpen(v => !v)} />

        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          <div className="w-full">
            <div className="grid grid-cols-1 xl:grid-cols-[1fr_280px] gap-6">

              {/* ── Left: main content ──────────────── */}
              <div className="flex flex-col gap-6 min-w-0">

                {/* Welcome + actions */}
                <div className="flex items-start justify-between">
                  <div>
                    <h1 className="text-2xl font-bold text-neutral-900">
                      Welcome back, {firstName}
                    </h1>
                    <p className="mt-1 text-sm text-neutral-500">
                      {isLoading
                        ? 'Loading your tasks…'
                        : taskCount > 0
                          ? `You have ${taskCount} pending task${taskCount > 1 ? 's' : ''} across your modules.`
                          : 'All caught up — no pending tasks.'}
                    </p>
                  </div>
                </div>

                {/* Module cards */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 auto-rows-fr gap-3 max-w-5xl">
                  {MODULES.map((m) => (
                    <ModuleCard
                      key={m.label}
                      icon={m.icon}
                      iconBg={m.iconBg}
                      label={m.label}
                      description={m.description}
                      href={m.href}
                      healthy={m.healthy}
                      loading={m.loading}
                      comingSoon={m.comingSoon}
                    />
                  ))}
                </div>

                {/* Task inbox */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-semibold text-neutral-800">My Task Inbox</h2>
                      {taskCount > 0 && (
                        <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-primary-600 px-1.5 text-[10px] font-bold text-white">
                          {taskCount > 99 ? '99+' : taskCount}
                        </span>
                      )}
                    </div>
                    {taskCount > 0 && (
                      <button className="text-xs text-primary-600 hover:text-primary-700 font-medium transition-colors">
                        Mark all as read
                      </button>
                    )}
                  </div>

                  {isLoading ? (
                    <div className="space-y-2">
                      {[1, 2, 3].map((i) => (
                        <div key={i} className="h-[68px] animate-pulse rounded-xl bg-neutral-100" />
                      ))}
                    </div>
                  ) : allTasks.length === 0 ? (
                    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-neutral-200 bg-white py-14 text-center">
                      <CheckCircle2 className="h-10 w-10 text-primary-200 mb-3" />
                      <p className="font-medium text-neutral-700">All caught up!</p>
                      <p className="mt-1 text-sm text-neutral-400">No pending tasks across any module.</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {allTasks.map((task) => (
                        <TaskRow key={task.id} task={task} />
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* ── Right: widgets ──────────────────── */}
              <div className="flex flex-col gap-4 xl:min-w-[280px]">
                <PlatformHealth
                  epmsHealth={epmsHealth.data}
                  oaHealth={oaHealth.data}
                />
                <RecentActivity tasks={allTasks} />
              </div>

            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
