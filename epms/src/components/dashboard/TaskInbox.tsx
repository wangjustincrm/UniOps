import { useNavigate } from 'react-router-dom'
import { AlertCircle, Clock, ArrowRight } from 'lucide-react'
import { cn, formatCAD, formatDate } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { isRetitledTask, taskCardLabel } from '@/lib/taskTypes'
import type { TaskItem } from '@/types'

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
        'rounded-lg border p-3 transition-shadow hover:shadow-sm',
        isUrgent ? 'border-danger-line bg-danger-soft' : 'border-line bg-surface'
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            {isUrgent && <AlertCircle className="h-3.5 w-3.5 shrink-0 text-danger-fg" />}
            <p className={cn('text-sm font-medium truncate', isUrgent ? 'text-danger-fg' : 'text-fg')}>
              {taskCardLabel(task)}
            </p>
          </div>
          <p className="text-xs text-fg-muted truncate">
            {task.documentNumber}
            {task.vendor ? ` · ${task.vendor}` : ''}
            {task.amount ? ` · ${formatCAD(task.amount)}` : ''}
          </p>
          {/* Credit-note instructions / AP's rejection note (ISS-0230, ISS-0259) */}
          {isRetitledTask(task) && task.description && (
            <p className="mt-1 text-xs text-fg-muted line-clamp-2">{task.description}</p>
          )}
          {task.dueDate && (
            <div className="mt-1 flex items-center gap-1 text-xs text-fg-muted">
              <Clock className="h-3 w-3" />
              Due {formatDate(task.dueDate)}
            </div>
          )}
          {task.daysWaiting != null && (
            <div className="mt-1 flex items-center gap-1 text-xs text-fg-muted">
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
        <h2 className="text-base font-semibold text-fg">
          To-Do List{' '}
          <span className="ml-1.5 font-mono text-sm font-normal tabular-nums text-fg-muted">
            {total}
          </span>
        </h2>
      </div>

      {total === 0 && (
        <div className="rounded-xl bg-surface shadow-sm py-10 text-center">
          <p className="text-sm text-fg-muted">No pending tasks</p>
        </div>
      )}

      {urgent.length > 0 && (
        <div>
          <p className="mb-2 flex items-center gap-1 text-xs font-semibold text-danger-fg">
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
          <p className="mb-2 text-xs font-semibold text-fg-muted">
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
        <Button variant="link" onClick={onViewAll} className="mt-1 self-start text-sm">
          View all {total} tasks →
        </Button>
      )}
    </div>
  )
}
