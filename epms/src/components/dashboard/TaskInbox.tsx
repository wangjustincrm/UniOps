import { useNavigate } from 'react-router-dom'
import { AlertCircle, Clock, ArrowRight } from 'lucide-react'
import { cn, formatCAD } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { TaskItem } from '@/types'

const TASK_TYPE_LABELS: Record<string, string> = {
  approve_pr: 'Approve Purchase Request',
  approve_po: 'Approve Purchase Order',
  approve_pa: 'Approve Payment Application',
  process_pa: 'Process Payment',
  revise_pr: 'Revise Purchase Request',
  revise_pa: 'Revise Payment Application',
  acknowledge_gr: 'Acknowledge Goods Receipt',
  collect_goods: 'Collect Goods',
  confirm_service_gr: 'Confirm Service Completion',
  settle_prepayment: 'Settle Prepayment',
  link_invoice: 'Link Invoice to PO',
  create_pa: 'Create Payment Application',
  create_prepayment_pa: 'Create Prepayment PA',
  approve_budget_plan: 'Approve Budget Plan',
  revise_budget_plan: 'Revise Budget Plan',
}

interface TaskCardProps {
  task: TaskItem
}

function TaskCard({ task }: TaskCardProps) {
  const navigate = useNavigate()
  const isUrgent = task.priority === 'urgent'

  // Absolute URLs (e.g. a Budget Plan task handing off to the Finance module)
  // can't go through react-router — jump the whole page instead.
  const goToTask = () => {
    if (/^https?:\/\//.test(task.href)) {
      window.location.assign(task.href)
    } else {
      navigate(task.href)
    }
  }

  return (
    <div
      className={cn(
        'rounded-lg border p-3 transition-shadow hover:shadow-[0_1px_3px_rgba(10,124,124,0.08)]',
        isUrgent ? 'border-danger-200 bg-danger-50' : 'border-neutral-200 bg-white'
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            {isUrgent && <AlertCircle className="h-3.5 w-3.5 shrink-0 text-danger-600" />}
            <p className={cn('text-sm font-medium truncate', isUrgent ? 'text-danger-700' : 'text-neutral-900')}>
              {TASK_TYPE_LABELS[task.type] ?? task.type}
            </p>
          </div>
          <p className="text-xs text-neutral-500 truncate">
            {task.documentNumber}
            {task.vendor ? ` · ${task.vendor}` : ''}
            {task.amount ? ` · ${formatCAD(task.amount)}` : ''}
          </p>
          {task.dueDate && (
            <div className="mt-1 flex items-center gap-1 text-xs text-neutral-400">
              <Clock className="h-3 w-3" />
              Due {task.dueDate}
            </div>
          )}
          {task.daysWaiting != null && (
            <div className="mt-1 flex items-center gap-1 text-xs text-neutral-400">
              <Clock className="h-3 w-3" />
              Waiting {task.daysWaiting}d
            </div>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={goToTask}
          aria-label="Go to task"
          className="shrink-0"
        >
          <ArrowRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

interface TaskInboxProps {
  tasks: TaskItem[]
  maxItems?: number
  onViewAll?: () => void
}

export function TaskInbox({ tasks, maxItems = 10, onViewAll }: TaskInboxProps) {
  const urgent = tasks.filter((t) => t.priority === 'urgent')
  const normal = tasks.filter((t) => t.priority === 'normal')
  const total = tasks.length

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-neutral-900">
          To-Do List{' '}
          <span className="ml-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary-600 px-1 text-[10px] font-bold text-white">
            {total}
          </span>
        </h2>
      </div>

      {total === 0 && (
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] py-10 text-center">
          <p className="text-sm text-neutral-400">No pending tasks</p>
        </div>
      )}

      {urgent.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-danger-600 flex items-center gap-1">
            <AlertCircle className="h-3.5 w-3.5" />
            Urgent ({urgent.length})
          </p>
          <div className="flex flex-col gap-2">
            {urgent.slice(0, maxItems).map((t) => (
              <TaskCard key={t.id} task={t} />
            ))}
          </div>
        </div>
      )}

      {normal.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Normal ({normal.length})
          </p>
          <div className="flex flex-col gap-2">
            {normal.slice(0, maxItems - urgent.length).map((t) => (
              <TaskCard key={t.id} task={t} />
            ))}
          </div>
        </div>
      )}

      {total > maxItems && (
        <button
          onClick={onViewAll}
          className="mt-1 text-sm text-primary-600 hover:underline text-left"
        >
          View all {total} tasks →
        </button>
      )}
    </div>
  )
}
