import { cn, formatDateTime } from '@/lib/utils'
import { Check, Clock, RotateCcw, SkipForward, X } from 'lucide-react'
import type { ApprovalStep } from '@/types'

const CHANNEL_BADGES: Record<string, string> = {
  Web: 'bg-primary-50 text-primary-600',
  Email: 'bg-surface-sunken text-fg-secondary',
  Teams: 'bg-cat4-soft text-cat4-fg',
}

/**
 * Outcome of the last "Send reminder" click, owned by the parent page.
 *
 * The button is only honest if it reports back: until it was wired up it called
 * `console.log` and rendered nothing, so a click was indistinguishable from a
 * working reminder. `idle` renders the plain link; everything else renders the
 * result underneath it.
 */
export interface ReminderState {
  status: 'idle' | 'pending' | 'success' | 'error'
  message?: string
}

interface ApprovalTimelineProps {
  steps: ApprovalStep[]
  onSendReminder?: (stepId: string) => void
  reminder?: ReminderState
}

export function ApprovalTimeline({ steps, onSendReminder, reminder }: ApprovalTimelineProps) {
  const reminderStatus = reminder?.status ?? 'idle'
  return (
    <div className="flex flex-col gap-0">
      {steps.map((step, i) => {
        const isLast = i === steps.length - 1

        return (
          <div key={step.id} className="flex gap-3">
            {/* Icon column */}
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm',
                  step.status === 'completed' && 'bg-success-solid text-white',
                  step.status === 'current' && 'bg-primary-600 text-white ring-4 ring-primary-100',
                  step.status === 'pending' && 'border-2 border-line-strong bg-surface text-fg-muted',
                  step.status === 'skipped' && 'border-2 border-dashed border-line-strong bg-surface text-fg-faint',
                  step.status === 'returned' && 'bg-warning-solid text-white',
                  step.status === 'rejected' && 'bg-danger-solid text-white'
                )}
              >
                {step.status === 'completed' && <Check className="h-3.5 w-3.5" strokeWidth={2.5} />}
                {step.status === 'current' && <Clock className="h-3.5 w-3.5" />}
                {step.status === 'pending' && <span className="h-2 w-2 rounded-full bg-surface-strong" />}
                {step.status === 'skipped' && <SkipForward className="h-3.5 w-3.5" />}
                {step.status === 'returned' && <RotateCcw className="h-3.5 w-3.5" />}
                {step.status === 'rejected' && <X className="h-3.5 w-3.5" strokeWidth={2.5} />}
              </div>
              {!isLast && (
                <div
                  className={cn(
                    'w-0.5 flex-1 min-h-4 mt-1',
                    step.status === 'completed' ? 'bg-success-300' : 'bg-surface-strong'
                  )}
                />
              )}
            </div>

            {/* Content */}
            <div className={cn('pb-4 min-w-0 flex-1', isLast && 'pb-0')}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p
                    className={cn(
                      'text-sm font-medium',
                      step.status === 'completed' && 'text-fg',
                      step.status === 'current' && 'text-primary-700',
                      step.status === 'pending' && 'text-fg-muted',
                      step.status === 'skipped' && 'text-fg-muted line-through',
                      step.status === 'returned' && 'text-warning-fg',
                      step.status === 'rejected' && 'text-danger-fg'
                    )}
                  >
                    {step.actorName ? `${step.role} — ${step.actorName}` : step.role}
                  </p>

                  {['completed', 'returned', 'rejected'].includes(step.status) && step.completedAt && (
                    <p className="text-xs text-fg-muted mt-0.5">{formatDateTime(step.completedAt)}</p>
                  )}

                  {step.status === 'current' && (
                    <div className="mt-0.5">
                      <p className="text-xs text-primary-600">Awaiting action</p>
                      {step.daysWaiting != null && (
                        <p className="text-xs text-fg-muted">
                          Waiting {step.daysWaiting} day{step.daysWaiting !== 1 ? 's' : ''}
                        </p>
                      )}
                    </div>
                  )}

                  {step.status === 'pending' && (
                    <p className="text-xs text-fg-muted mt-0.5">{step.role}</p>
                  )}
                </div>

                <div className="flex items-center gap-1.5 shrink-0">
                  {step.channel && (
                    <span className={cn('rounded-full px-1.5 py-0.5 text-2xs font-medium', CHANNEL_BADGES[step.channel])}>
                      {step.channel}
                    </span>
                  )}
                  {step.action && (
                    <span
                      className={cn(
                        'rounded-full px-1.5 py-0.5 text-2xs font-medium',
                        step.action === 'Approved' && 'bg-success-soft text-success-fg',
                        step.action === 'Returned' && 'bg-warning-soft text-warning-fg',
                        step.action === 'Rejected' && 'bg-danger-soft text-danger-fg'
                      )}
                    >
                      {step.action}
                    </span>
                  )}
                </div>
              </div>

              {step.comment && (
                <p className="mt-1 text-xs text-fg-muted italic">"{step.comment}"</p>
              )}

              {/* Unquoted: the system explaining a skip or a return, not a person. */}
              {step.note && (
                <p className="mt-1 text-xs text-fg-muted">{step.note}</p>
              )}

              {step.status === 'current' && onSendReminder && (
                <div className="mt-1.5">
                  <button
                    onClick={() => onSendReminder(step.id)}
                    disabled={reminderStatus === 'pending'}
                    className="text-xs text-primary-600 hover:underline disabled:cursor-not-allowed disabled:text-fg-muted disabled:no-underline"
                  >
                    {reminderStatus === 'pending' ? 'Sending reminder…' : 'Send reminder'}
                  </button>
                  {reminder?.message && reminderStatus !== 'pending' && (
                    <p
                      className={cn(
                        'mt-1 text-xs',
                        reminderStatus === 'success' ? 'text-success-fg' : 'text-warning-fg'
                      )}
                    >
                      {reminder.message}
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
