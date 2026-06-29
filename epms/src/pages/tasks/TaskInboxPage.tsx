import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, Clock, ArrowRight, Check, Inbox } from 'lucide-react'
import { cn, formatCAD, formatDate } from '@/lib/utils'
import { financeHandoffHref } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { useTasks, useCompleteTask } from '@/hooks/useTasks'
import type { TaskType } from '@/types'
import type { ApiTask } from '@/services/tasks'

// ─── Constants ────────────────────────────────────────────────────────────────

const TASK_TYPE_LABELS: Record<string, string> = {
  approve_pr: 'Approve Purchase Request',
  approve_po: 'Approve Purchase Order',
  approve_pa: 'Approve Payment Application',
  process_pa: 'Process Payment',
  revise_pr: 'Revise Purchase Request',
  revise_po: 'Revise Purchase Order',
  revise_pa: 'Revise Payment Application',
  create_po: 'Create Purchase Order',
  place_order: 'Place Order',
  acknowledge_gr: 'Acknowledge Goods Receipt',
  gr_damage_report: 'Damaged / Discrepancy Goods',
  collect_goods: 'Collect Goods',
  confirm_service_gr: 'Confirm Service Completion',
  settle_prepayment: 'Settle Prepayment',
  link_invoice: 'Link Invoice to PO',
  create_pa: 'Create Payment Application',
  approve_budget_plan: 'Approve Budget Plan',
  revise_budget_plan: 'Revise Budget Plan',
}

const ALL_TASK_TYPES: TaskType[] = [
  'approve_pr',
  'approve_po',
  'approve_pa',
  'process_pa',
  'revise_pr',
  'revise_pa',
  'create_po',
  'place_order',
  'revise_po',
  'acknowledge_gr',
  'gr_damage_report',
  'collect_goods',
  'confirm_service_gr',
  'settle_prepayment',
  'link_invoice',
  'create_pa',
]

type TabValue = 'all' | 'urgent' | 'normal' | 'completed'

const TABS: { value: TabValue; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'urgent', label: 'Urgent' },
  { value: 'normal', label: 'Normal' },
  { value: 'completed', label: 'Completed' },
]

// ─── Empty state messages ─────────────────────────────────────────────────────

const EMPTY_MESSAGES: Record<TabValue, { heading: string; sub: string }> = {
  all: { heading: 'All caught up!', sub: 'You have no pending tasks right now.' },
  urgent: { heading: 'No urgent tasks', sub: 'Nothing needs your immediate attention.' },
  normal: { heading: 'No normal tasks', sub: 'Your normal task queue is clear.' },
  completed: { heading: 'No completed tasks today', sub: "Tasks you finish today will appear here." },
}

// ─── Full task card ───────────────────────────────────────────────────────────

const HREF_MAP: Record<string, string> = {
  pr: '/pr', po: '/po', gr: '/gr', invoice: '/invoices', pa: '/pa',
}

interface FullTaskCardProps {
  task: ApiTask
  onComplete: () => void
}

function FullTaskCard({ task, onComplete }: FullTaskCardProps) {
  const navigate = useNavigate()
  const isUrgent = task.priority === 'urgent'
  const isDone = task.is_completed

  // Budget Plans are owned by the Finance module. The plan pages physically live
  // in EPMS but are surfaced through Finance (Portal chrome) via EpmsEmbed, so a
  // budget_plan task must open in Finance — a full-page handoff — rather than an
  // in-app navigate that would strand the user on EPMS's bare budget page.
  const isBudgetPlan = task.document_type.toLowerCase() === 'budget_plan'

  const goToTask = () => {
    if (isBudgetPlan) {
      window.location.assign(financeHandoffHref(`/budget/plans/${task.document_id}`))
      return
    }
    const href = task.type === 'create_pa'
      ? `/pa/create?poId=${task.document_id}`
      : `${HREF_MAP[task.document_type.toLowerCase()] ?? '/'}/${task.document_id}`
    navigate(href)
  }

  return (
    <div
      className={cn(
        'rounded-xl border p-4 transition-shadow',
        isDone
          ? 'border-neutral-200 bg-neutral-50'
          : isUrgent
          ? 'border-danger-200 bg-danger-50 hover:shadow-[0_2px_8px_rgba(185,28,28,0.08)]'
          : 'border-neutral-200 bg-white hover:shadow-[0_2px_8px_rgba(10,124,124,0.08)]'
      )}
    >
      <div className="flex items-start justify-between gap-3">
        {/* Left content */}
        <div className="flex-1 min-w-0">
          {/* Type label + urgent icon + done badge */}
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            {isUrgent && !isDone && (
              <AlertCircle className="h-4 w-4 shrink-0 text-danger-600" />
            )}
            <p
              className={cn(
                'text-sm font-semibold',
                isDone
                  ? 'line-through text-neutral-400'
                  : isUrgent
                  ? 'text-danger-700'
                  : 'text-neutral-900'
              )}
            >
              {TASK_TYPE_LABELS[task.type] ?? task.type}
            </p>
            {isDone && (
              <span className="inline-flex items-center gap-1 rounded-full bg-success-100 px-2 py-0.5 text-[11px] font-medium text-success-700">
                <Check className="h-3 w-3" />
                Done
              </span>
            )}
          </div>

          {/* Document + vendor + amount */}
          <p
            className={cn(
              'text-sm',
              isDone ? 'line-through text-neutral-400' : 'text-neutral-600'
            )}
          >
            {task.document_number}
            {task.vendor ? ` · ${task.vendor}` : ''}
            {task.amount ? ` · ${formatCAD(task.amount)}` : ''}
          </p>

          {/* Due date */}
          {!isDone && task.due_date && (
            <div className="mt-1.5 flex items-center gap-1 text-xs text-neutral-400">
              <Clock className="h-3.5 w-3.5" />
              Due {task.due_date}
            </div>
          )}

          {/* Completed info */}
          {isDone && task.completed_at && (
            <p className="mt-1 text-xs text-neutral-400">
              Completed {formatDate(task.completed_at)}
            </p>
          )}
        </div>

        {/* Right actions */}
        <div className="flex items-center gap-1.5 shrink-0">
          {!isDone && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onComplete}
              aria-label="Mark task as done"
              className="text-neutral-500 hover:text-success-600 hover:bg-success-50"
            >
              <Check className="h-4 w-4" />
              Mark Done
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={goToTask}
            aria-label="Go to task"
          >
            <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── Empty state ──────────────────────────────────────────────────────────────

function EmptyState({ tab }: { tab: TabValue }) {
  const msg = EMPTY_MESSAGES[tab]
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Inbox className="h-10 w-10 text-neutral-300 mb-3" />
      <p className="text-base font-medium text-neutral-500">{msg.heading}</p>
      <p className="mt-1 text-sm text-neutral-400">{msg.sub}</p>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function TaskInboxPage() {
  const completeTask = useCompleteTask()

  const [activeTab, setActiveTab] = useState<TabValue>('all')
  const [typeFilter, setTypeFilter] = useState<TaskType | 'all'>('all')

  // Today's date prefix (YYYY-MM-DD)
  const todayPrefix = new Date().toISOString().slice(0, 10)

  // Fetch all tasks (open + completed today)
  const { data: openData } = useTasks({ is_completed: false })
  const { data: completedData } = useTasks({ is_completed: true })
  const openTasks = openData?.items ?? []
  const completedTasks = (completedData?.items ?? []).filter(
    (t) => t.completed_at && t.completed_at.startsWith(todayPrefix)
  )
  const allTasks = [...openTasks, ...completedTasks]

  // Derived counts
  const urgentCount = openTasks.filter((t) => t.priority === 'urgent').length
  const normalCount = openTasks.filter((t) => t.priority === 'normal').length
  const completedTodayCount = completedTasks.length

  // Filter by tab
  const tabFiltered = allTasks.filter((t) => {
    if (activeTab === 'urgent') return !t.is_completed && t.priority === 'urgent'
    if (activeTab === 'normal') return !t.is_completed && t.priority === 'normal'
    if (activeTab === 'completed') return t.is_completed
    return !t.is_completed
  })

  // Filter by type
  const filtered =
    typeFilter === 'all' ? tabFiltered : tabFiltered.filter((t) => t.type === typeFilter)

  const handleComplete = (id: string) => {
    completeTask.mutate(id)
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      {/* ── Header ── */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-neutral-900">Task Inbox</h1>
        <p className="mt-1 text-sm text-neutral-500">Your pending actions</p>
      </div>

      {/* ── Stats bar ── */}
      <div className="mb-6 grid grid-cols-3 gap-4">
        {/* Urgent */}
        <div className="rounded-xl border border-danger-200 bg-danger-50 px-4 py-3">
          <div className="flex items-center gap-1.5 mb-1">
            <AlertCircle className="h-4 w-4 text-danger-600" />
            <p className="text-xs font-semibold uppercase tracking-wide text-danger-600">Urgent</p>
          </div>
          <p className="text-2xl font-bold text-danger-700">{urgentCount}</p>
          <p className="text-xs text-danger-500 mt-0.5">
            {urgentCount === 1 ? 'task' : 'tasks'} needing attention
          </p>
        </div>

        {/* Normal */}
        <div className="rounded-xl border border-neutral-200 bg-white px-4 py-3">
          <div className="flex items-center gap-1.5 mb-1">
            <Clock className="h-4 w-4 text-neutral-400" />
            <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Normal</p>
          </div>
          <p className="text-2xl font-bold text-neutral-700">{normalCount}</p>
          <p className="text-xs text-neutral-400 mt-0.5">
            {normalCount === 1 ? 'task' : 'tasks'} in queue
          </p>
        </div>

        {/* Completed today */}
        <div className="rounded-xl border border-success-200 bg-success-50 px-4 py-3">
          <div className="flex items-center gap-1.5 mb-1">
            <Check className="h-4 w-4 text-success-600" />
            <p className="text-xs font-semibold uppercase tracking-wide text-success-600">
              Completed
            </p>
          </div>
          <p className="text-2xl font-bold text-success-700">{completedTodayCount}</p>
          <p className="text-xs text-success-600 mt-0.5">
            {completedTodayCount === 1 ? 'task' : 'tasks'} done today
          </p>
        </div>
      </div>

      {/* ── Filter controls ── */}
      <div className="mb-4 flex items-center justify-between gap-3 flex-wrap">
        {/* Tab strip */}
        <div className="flex items-center gap-1 rounded-lg bg-neutral-100 p-1">
          {TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => setActiveTab(tab.value)}
              className={cn(
                'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                activeTab === tab.value
                  ? 'bg-white text-neutral-900 shadow-sm'
                  : 'text-neutral-500 hover:text-neutral-700'
              )}
            >
              {tab.label}
              {tab.value === 'urgent' && urgentCount > 0 && (
                <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-danger-600 px-1 text-[10px] font-bold text-white">
                  {urgentCount}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Type filter */}
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value as TaskType | 'all')}
          className="h-9 rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
        >
          <option value="all">All Types</option>
          {ALL_TASK_TYPES.map((t) => (
            <option key={t} value={t}>
              {TASK_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </div>

      {/* ── Task list ── */}
      {filtered.length === 0 ? (
        <EmptyState tab={activeTab} />
      ) : (
        <div className="flex flex-col gap-3">
          {filtered.map((task) => (
            <FullTaskCard
              key={task.id}
              task={task}
              onComplete={() => handleComplete(task.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
