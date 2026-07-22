import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle2, Clock, AlertTriangle, ArrowRight,
  Inbox, CreditCard, Receipt, RotateCcw, Banknote,
} from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api } from '@/lib/api'
import { groupTasks } from '@/lib/groupTasks'

// ── Types ─────────────────────────────────────────────────────────────────────

interface OaTaskItem {
  id: string
  task_type: string
  doc_type: string
  doc_id: string
  doc_number: string
  title: string
  submitter_name: string
  amount: number
  currency: string
  status: string
  submitted_at: string | null
  created_at: string
  is_own: boolean
}

interface OaTaskListResponse {
  items: OaTaskItem[]
  total: number
}

// ── Config ────────────────────────────────────────────────────────────────────

const TASK_META: Record<string, {
  label: string
  icon: React.ReactNode
  border: string
  bg: string
  badge: string
  text: string
}> = {
  approve_expense: {
    label: 'Approve Expense',
    icon: <Receipt className="h-4 w-4" />,
    border: 'border-primary-200',
    bg: 'bg-primary-50',
    badge: 'bg-primary-100 text-primary-700',
    text: 'text-primary-700',
  },
  approve_pa: {
    label: 'Approve Payment',
    icon: <CreditCard className="h-4 w-4" />,
    border: 'border-primary-200',
    bg: 'bg-primary-50',
    badge: 'bg-primary-100 text-primary-700',
    text: 'text-primary-700',
  },
  revise_expense: {
    label: 'Revise Expense',
    icon: <RotateCcw className="h-4 w-4" />,
    border: 'border-warning-200',
    bg: 'bg-warning-50',
    badge: 'bg-warning-100 text-warning-700',
    text: 'text-warning-700',
  },
  revise_pa: {
    label: 'Revise Payment',
    icon: <RotateCcw className="h-4 w-4" />,
    border: 'border-warning-200',
    bg: 'bg-warning-50',
    badge: 'bg-warning-100 text-warning-700',
    text: 'text-warning-700',
  },
  pay_expense: {
    label: 'Record Payment',
    icon: <Banknote className="h-4 w-4" />,
    border: 'border-info-200',
    bg: 'bg-info-50',
    badge: 'bg-info-100 text-info-700',
    text: 'text-info-700',
  },
  pay_pa: {
    label: 'Record Payment',
    icon: <Banknote className="h-4 w-4" />,
    border: 'border-info-200',
    bg: 'bg-info-50',
    badge: 'bg-info-100 text-info-700',
    text: 'text-info-700',
  },
  submitted_expense: {
    label: 'In Review',
    icon: <Clock className="h-4 w-4" />,
    border: 'border-neutral-200',
    bg: 'bg-white',
    badge: 'bg-neutral-100 text-neutral-600',
    text: 'text-neutral-600',
  },
  submitted_pa: {
    label: 'In Review',
    icon: <Clock className="h-4 w-4" />,
    border: 'border-neutral-200',
    bg: 'bg-white',
    badge: 'bg-neutral-100 text-neutral-600',
    text: 'text-neutral-600',
  },
}

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  submitted: 'Submitted',
  in_review: 'In Review',
  approved: 'Approved',
  paid: 'Paid',
  returned: 'Returned',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
}

const DOC_TYPE_LABELS: Record<string, string> = {
  exp: 'Expense',
  mil: 'Mileage',
  trv: 'Travel',
  cfm: 'Custom Form',
  pa: 'PO Payment',
  pa_dir: 'Direct Payment',
}

type Tab = 'all' | 'action' | 'mine'

const TABS: { value: Tab; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'action', label: 'Needs My Action' },
  { value: 'mine', label: 'My Submissions' },
]

const ACTION_TYPES = new Set(['approve_expense', 'approve_pa', 'revise_expense', 'revise_pa', 'pay_expense', 'pay_pa'])

// Fixed display order for task-type groups. Types not listed fall to the end.
const GROUP_ORDER = [
  'approve_expense', 'approve_pa',
  'revise_expense', 'revise_pa',
  'pay_expense', 'pay_pa',
  'submitted_expense', 'submitted_pa',
]

// ── Task card ─────────────────────────────────────────────────────────────────

function TaskCard({ task }: { task: OaTaskItem }) {
  const navigate = useNavigate()
  const meta = TASK_META[task.task_type] ?? TASK_META.submitted_expense

  const href = task.doc_type === 'pa' || task.doc_type === 'pa_dir'
    ? `/pa/${task.doc_id}`
    : `/expenses/${task.doc_id}`

  return (
    <div
      className={cn(
        'rounded-xl border p-4 transition-shadow hover:shadow-[0_2px_8px_rgba(8,94,94,0.08)] cursor-pointer',
        meta.border, meta.bg,
      )}
      onClick={() => navigate(href)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {/* Type badge + doc type chip */}
          <div className="flex items-center gap-2 mb-1.5 flex-wrap">
            <span className={cn(
              'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold',
              meta.badge,
            )}>
              {meta.icon}
              {meta.label}
            </span>
            <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
              {DOC_TYPE_LABELS[task.doc_type] ?? task.doc_type.toUpperCase()}
            </span>
          </div>

          {/* Doc number + submitter + amount */}
          <p className="text-sm font-semibold text-neutral-900 font-mono">
            {task.doc_number}
          </p>
          <p className="mt-0.5 text-sm text-neutral-500 truncate">
            {task.submitter_name}
            {task.title && task.title !== task.submitter_name
              ? ` · ${task.title}`
              : ''}
          </p>

          {/* Meta row */}
          <div className="mt-1.5 flex items-center gap-3 text-xs text-neutral-400 flex-wrap">
            <span className="font-medium text-neutral-700">
              {formatAmount(task.amount, task.currency)}
            </span>
            <span className={cn(
              'rounded-full px-1.5 py-0.5 text-[11px] font-medium',
              task.status === 'returned'
                ? 'bg-warning-100 text-warning-700'
                : task.status === 'approved'
                ? 'bg-success-100 text-success-700'
                : 'bg-neutral-100 text-neutral-600',
            )}>
              {STATUS_LABELS[task.status] ?? task.status}
            </span>
            {task.submitted_at && (
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatDate(task.submitted_at)}
              </span>
            )}
          </div>
        </div>

        <button
          className="shrink-0 rounded-lg p-1.5 text-neutral-400 hover:bg-white/80 hover:text-primary-600 transition-colors"
          aria-label="Open document"
          onClick={(e) => { e.stopPropagation(); navigate(href) }}
        >
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ tab }: { tab: Tab }) {
  const messages: Record<Tab, { heading: string; sub: string }> = {
    all: { heading: 'All caught up', sub: 'No pending OA items.' },
    action: { heading: 'Nothing needs your action', sub: 'No approvals, revisions, or payments pending.' },
    mine: { heading: 'No submissions in progress', sub: 'Your submitted items will appear here.' },
  }
  const { heading, sub } = messages[tab]
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Inbox className="h-10 w-10 text-neutral-300 mb-3" />
      <p className="text-base font-medium text-neutral-500">{heading}</p>
      <p className="mt-1 text-sm text-neutral-400">{sub}</p>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TaskListPage() {
  const [tab, setTab] = useState<Tab>('all')
  const [docTypeFilter, setDocTypeFilter] = useState('all')

  const { data, isLoading } = useQuery<OaTaskListResponse>({
    queryKey: ['oa-tasks'],
    queryFn: () => api.get('/api/v1/tasks'),
    refetchInterval: 60_000,
  })

  const allItems = data?.items ?? []

  // Stat counts
  const actionCount = allItems.filter(t => ACTION_TYPES.has(t.task_type)).length
  const mineCount   = allItems.filter(t => t.is_own && (t.task_type === 'submitted_expense' || t.task_type === 'submitted_pa')).length
  const approveCount = allItems.filter(t => t.task_type === 'approve_expense' || t.task_type === 'approve_pa').length
  const reviseCount  = allItems.filter(t => t.task_type === 'revise_expense' || t.task_type === 'revise_pa').length
  const payCount     = allItems.filter(t => t.task_type === 'pay_expense' || t.task_type === 'pay_pa').length

  // Tab filter
  const tabFiltered = allItems.filter(t => {
    if (tab === 'action') return ACTION_TYPES.has(t.task_type)
    if (tab === 'mine')   return t.is_own && (t.task_type === 'submitted_expense' || t.task_type === 'submitted_pa')
    return true
  })

  // Doc type filter
  const filtered = docTypeFilter === 'all'
    ? tabFiltered
    : tabFiltered.filter(t => t.doc_type === docTypeFilter)

  const groups = groupTasks(
    filtered,
    (t) => t.task_type,
    (k) => TASK_META[k]?.label ?? k,
    GROUP_ORDER,
  )

  return (
    <div className="mx-auto max-w-3xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-neutral-900">Task Inbox</h1>
        <p className="mt-1 text-sm text-neutral-500">All pending OA actions</p>
      </div>

      {/* Stats */}
      <div className="mb-6 grid grid-cols-3 gap-4">
        <div className="rounded-xl border border-primary-200 bg-primary-50 px-4 py-3">
          <div className="flex items-center gap-1.5 mb-1">
            <CheckCircle2 className="h-4 w-4 text-primary-600" />
            <p className="text-xs font-semibold uppercase tracking-wide text-primary-600">Approve</p>
          </div>
          <p className="text-2xl font-bold text-primary-700">{approveCount}</p>
          <p className="text-xs text-primary-500 mt-0.5">pending approval</p>
        </div>

        <div className="rounded-xl border border-warning-200 bg-warning-50 px-4 py-3">
          <div className="flex items-center gap-1.5 mb-1">
            <AlertTriangle className="h-4 w-4 text-warning-600" />
            <p className="text-xs font-semibold uppercase tracking-wide text-warning-600">Revise</p>
          </div>
          <p className="text-2xl font-bold text-warning-700">{reviseCount}</p>
          <p className="text-xs text-warning-500 mt-0.5">returned to you</p>
        </div>

        <div className="rounded-xl border border-info-200 bg-info-50 px-4 py-3">
          <div className="flex items-center gap-1.5 mb-1">
            <Banknote className="h-4 w-4 text-info-600" />
            <p className="text-xs font-semibold uppercase tracking-wide text-info-600">Pay</p>
          </div>
          <p className="text-2xl font-bold text-info-700">{payCount}</p>
          <p className="text-xs text-info-500 mt-0.5">awaiting payment</p>
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex items-center justify-between gap-3 flex-wrap">
        {/* Tab strip */}
        <div className="flex items-center gap-1 rounded-lg bg-neutral-100 p-1">
          {TABS.map(t => (
            <button
              key={t.value}
              onClick={() => setTab(t.value)}
              className={cn(
                'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                tab === t.value
                  ? 'bg-white text-neutral-900 shadow-sm'
                  : 'text-neutral-500 hover:text-neutral-700',
              )}
            >
              {t.label}
              {t.value === 'action' && actionCount > 0 && (
                <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary-600 px-1 text-[10px] font-bold text-white">
                  {actionCount}
                </span>
              )}
              {t.value === 'mine' && mineCount > 0 && (
                <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-neutral-400 px-1 text-[10px] font-bold text-white">
                  {mineCount}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Doc type filter */}
        <select
          value={docTypeFilter}
          onChange={e => setDocTypeFilter(e.target.value)}
          className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
        >
          <option value="all">All Types</option>
          {Object.entries(DOC_TYPE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </div>

      {/* List (grouped by task type) */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-neutral-100" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState tab={tab} />
      ) : (
        <div className="flex flex-col gap-6">
          {groups.map(group => (
            <section key={group.key}>
              <div className="mb-2 flex items-center gap-2">
                <h3 className="text-sm font-semibold text-neutral-700">{group.label}</h3>
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[11px] font-semibold text-neutral-500">
                  {group.items.length}
                </span>
              </div>
              <div className="flex flex-col gap-3">
                {group.items.map(task => (
                  <TaskCard key={task.id} task={task} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
