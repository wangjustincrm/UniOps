import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  UserCheck,
  ArrowRight, CheckCircle2, AlertCircle,
  Briefcase, CreditCard, Activity, Cloud, Landmark, CalendarClock,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { epmsApi, oaApi, EPMS_URL, OA_URL, VMS_URL, FINANCE_URL, BOOKING_URL, encodeSession } from '@/lib/api'
import { cn, formatAmount, timeAgo } from '@/lib/utils'
import { groupTasks } from '@/lib/groupTasks'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { PortalSidebar } from '@/components/layout/PortalSidebar'
import { TopHeader } from '@/components/layout/TopHeader'
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
  /** Task-type bucket key within a module (raw type / status / doc_type). */
  groupKey: string
  /** Human label for the group header. */
  groupLabel: string
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

const MODULE_ORDER: UnifiedTask['module'][] = ['EPMS', 'EXPENSE', 'VMS']

const MODULE_HEADING: Record<UnifiedTask['module'], string> = {
  EPMS: 'EPMS', EXPENSE: 'Expense', VMS: 'Visitor',
}

// EPMS engine task types → display labels (inlined; portal has no taskTypes.ts).
const EPMS_TYPE_LABELS: Record<string, string> = {
  create_pr: 'Create Purchase Request',
  create_po: 'Create Purchase Order',
  create_pa: 'Create Payment Application',
  create_prepayment_pa: 'Create Prepayment PA',
  approve_pr: 'Approve Purchase Request',
  approve_po: 'Approve Purchase Order',
  approve_pa: 'Approve Payment Application',
  place_order: 'Place Order',
  confirm_settlement: 'Confirm Settlement',
  review_match: 'Review Invoice Match',
  match_invoice: 'Match Invoice to PO',
  process_pa: 'Process Payment Application',
  revise_pr: 'Revise Purchase Request',
  revise_po: 'Revise Purchase Order',
  revise_pa: 'Revise Payment Application',
  acknowledge_gr: 'Acknowledge Goods Receipt',
  collect_goods: 'Collect Goods',
  confirm_service_gr: 'Confirm Service Receipt',
  gr_damage_report: 'Report Goods Damage',
  approve_budget_plan: 'Approve Budget Plan',
  revise_budget_plan: 'Revise Budget Plan',
}

const VMS_DOC_LABELS: Record<string, string> = {
  vms_visit: 'Visit Approvals',
  vms_train: 'Training Confirmations',
  vms_ppe:   'PPE Confirmations',
}

// Within-module group order. Keys are scoped by module in practice (an OA status
// never collides with an EPMS type), so one flat list is unambiguous. Unlisted
// keys fall to the end.
const GROUP_ORDER = [
  // EPMS
  'approve_pr', 'approve_po', 'approve_pa', 'approve_budget_plan',
  'process_pa', 'place_order',
  'create_pr', 'create_po', 'create_pa', 'create_prepayment_pa',
  'acknowledge_gr', 'collect_goods', 'confirm_service_gr', 'gr_damage_report',
  'review_match', 'match_invoice', 'confirm_settlement',
  'revise_pr', 'revise_po', 'revise_pa', 'revise_budget_plan',
  // Expense (OA my-actions statuses)
  'submitted', 'in_review', 'approved',
  // VMS
  'vms_visit', 'vms_train', 'vms_ppe',
]

function humanize(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
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
      groupKey: t.status,
      groupLabel: STATUS_LABEL[t.status] ?? humanize(t.status),
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
      let groupKey: string
      let groupLabel: string
      if (module === 'VMS') {
        groupKey = t.document_type
        groupLabel = VMS_DOC_LABELS[t.document_type] ?? humanize(t.document_type)
      } else {
        // EPMS-owned, or OA-owned surfaced via approval-api: bucket by engine task type.
        groupKey = t.type
        groupLabel = EPMS_TYPE_LABELS[t.type] ?? humanize(t.type)
      }
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
        groupKey,
        groupLabel,
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
                    <div className="space-y-6">
                      {MODULE_ORDER.map((mod) => {
                        const modTasks = allTasks.filter((t) => t.module === mod)
                        if (modTasks.length === 0) return null
                        const groups = groupTasks(
                          modTasks,
                          (t) => t.groupKey,
                          (_k, sample) => sample.groupLabel,
                          GROUP_ORDER,
                        )
                        const style = MODULE_STYLE[mod]
                        return (
                          <div key={mod} className="space-y-3">
                            {/* Module header */}
                            <div className="flex items-center gap-2 border-b border-neutral-200 pb-1.5">
                              <span className={cn('text-xs font-bold uppercase tracking-wider', style.badge)}>
                                {MODULE_HEADING[mod]}
                              </span>
                              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[10px] font-bold text-neutral-500">
                                {modTasks.length}
                              </span>
                            </div>
                            {/* Type sub-groups */}
                            {groups.map((group) => (
                              <section key={group.key}>
                                <div className="mb-1.5 flex items-center gap-2 pl-0.5">
                                  <h3 className="text-xs font-semibold text-neutral-600">{group.label}</h3>
                                  <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-neutral-100 px-1 text-[10px] font-semibold text-neutral-400">
                                    {group.items.length}
                                  </span>
                                </div>
                                <div className="space-y-2">
                                  {group.items.map((task) => (
                                    <TaskRow key={task.id} task={task} />
                                  ))}
                                </div>
                              </section>
                            ))}
                          </div>
                        )
                      })}
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
