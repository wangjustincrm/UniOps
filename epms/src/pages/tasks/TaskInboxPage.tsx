import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, Clock, ArrowRight, Check, Inbox } from 'lucide-react'
import { cn, formatCAD, formatDate } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useTasks } from '@/hooks/useTasks'
import { TASK_TYPE_LABELS, taskHref } from '@/lib/taskTypes'
import type { TaskType } from '@/lib/taskTypes'
import type { ApiTask } from '@/services/tasks'
import { groupTasks } from '@/lib/groupTasks'

// ─── Constants ────────────────────────────────────────────────────────────────

type TabValue = 'all' | 'urgent' | 'normal' | 'completed'

const TABS: { value: TabValue; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'urgent', label: 'Urgent' },
  { value: 'normal', label: 'Normal' },
  { value: 'completed', label: 'Completed' },
]

// Fixed display order for task-type groups (approvals → order → creates →
// match → settlement). Types not listed fall to the end.
const GROUP_ORDER: string[] = [
  'approve_pr', 'approve_po', 'approve_pa', 'approve_budget_plan',
  'process_pa', 'place_order',
  'create_pr', 'create_po', 'create_pa', 'create_prepayment_pa',
  'acknowledge_gr', 'collect_goods', 'confirm_service_gr', 'gr_damage_report',
  'review_match', 'match_invoice',
  'confirm_settlement',
  'revise_pr', 'revise_po', 'revise_pa', 'revise_budget_plan',
]

// ─── Empty state messages ─────────────────────────────────────────────────────

const EMPTY_MESSAGES: Record<TabValue, { heading: string; sub: string }> = {
  all: { heading: 'All caught up!', sub: 'You have no pending tasks right now.' },
  urgent: { heading: 'No urgent tasks', sub: 'Nothing needs your immediate attention.' },
  normal: { heading: 'No normal tasks', sub: 'Your normal task queue is clear.' },
  completed: { heading: 'No completed tasks today', sub: "Tasks you finish today will appear here." },
}

// ─── Full task card ───────────────────────────────────────────────────────────

interface FullTaskCardProps {
  task: ApiTask
}

function FullTaskCard({ task }: FullTaskCardProps) {
  const navigate = useNavigate()
  const isUrgent = task.priority === 'urgent'
  const isDone = task.is_completed

  // Budget Plans are owned by the Finance module. The plan pages physically live
  // in EPMS but are surfaced through Finance (Portal chrome) via EpmsEmbed, so a
  // budget_plan task must open in Finance — a full-page handoff (absolute URL) —
  // rather than an in-app navigate that would strand the user on EPMS's bare
  // budget page. taskHref() returns the absolute URL for those.
  const goToTask = () => {
    const href = taskHref(task)
    if (/^https?:\/\//.test(href)) {
      window.location.assign(href)
    } else {
      navigate(href)
    }
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
              {TASK_TYPE_LABELS[task.type as TaskType] ?? task.type}
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

          {/* Reason — NC sync error tasks only. Their whole point is the
              explanation (which ERP supplier is missing, what to do next), and
              it lives nowhere else: the subject is an NC order that is not in
              EPMS, so there is no document page to open and read. Every other
              task type has a boilerplate description ("Step 5/6: ...") that
              would only add noise here. */}
          {task.document_type === 'nc_sync' && task.description && (
            <p
              className={cn(
                'mt-2 whitespace-pre-line text-xs leading-relaxed',
                isDone ? 'text-neutral-400' : 'text-neutral-500'
              )}
            >
              {task.description}
            </p>
          )}

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

        {/* Right actions — a task is closed by DOING it (approve / place order /
            match / …) on the document page, never by dismissing it here. The old
            "Mark Done" button flipped tasks.is_completed straight through
            POST /tasks/{id}/complete, bypassing the approval engine: an approve_*
            task closed that way left the document in_review with no open task, so
            the Approve button disappeared for everyone (incl. system_admin) and the
            document was stranded. Removed — only the deep-link remains. */}
        <div className="flex items-center gap-1.5 shrink-0">
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
  const [activeTab, setActiveTab] = useState<TabValue>('all')

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

  const groups = groupTasks(
    tabFiltered,
    (t) => t.type,
    (k) => TASK_TYPE_LABELS[k] ?? k,
    GROUP_ORDER,
  )

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
      </div>

      {/* ── Task list (grouped by type) ── */}
      {tabFiltered.length === 0 ? (
        <EmptyState tab={activeTab} />
      ) : (
        <div className="flex flex-col gap-6">
          {groups.map((group) => (
            <section key={group.key}>
              <div className="mb-2 flex items-center gap-2">
                <h3 className="text-sm font-semibold text-neutral-700">{group.label}</h3>
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[11px] font-semibold text-neutral-500">
                  {group.items.length}
                </span>
              </div>
              <div className="flex flex-col gap-3">
                {group.items.map((task) => (
                  <FullTaskCard key={task.id} task={task} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
