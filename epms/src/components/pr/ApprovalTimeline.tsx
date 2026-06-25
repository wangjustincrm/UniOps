import { cn, formatDateTime } from '@/lib/utils'
import { Check, Clock, SkipForward } from 'lucide-react'
import type { ApprovalStep } from '@/types'

const CHANNEL_BADGES: Record<string, string> = {
  Web: 'bg-primary-50 text-primary-600',
  Email: 'bg-neutral-100 text-neutral-600',
  Teams: 'bg-violet-50 text-violet-600',
}

interface ApprovalTimelineProps {
  steps: ApprovalStep[]
  onSendReminder?: (stepId: string) => void
}

export function ApprovalTimeline({ steps, onSendReminder }: ApprovalTimelineProps) {
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
                  step.status === 'completed' && 'bg-success-600 text-white',
                  step.status === 'current' && 'bg-primary-600 text-white ring-4 ring-primary-100',
                  step.status === 'pending' && 'border-2 border-neutral-300 bg-white text-neutral-400',
                  step.status === 'skipped' && 'border-2 border-dashed border-neutral-300 bg-white text-neutral-300'
                )}
              >
                {step.status === 'completed' && <Check className="h-3.5 w-3.5" strokeWidth={2.5} />}
                {step.status === 'current' && <Clock className="h-3.5 w-3.5" />}
                {step.status === 'pending' && <span className="h-2 w-2 rounded-full bg-neutral-300" />}
                {step.status === 'skipped' && <SkipForward className="h-3.5 w-3.5" />}
              </div>
              {!isLast && (
                <div
                  className={cn(
                    'w-0.5 flex-1 min-h-4 mt-1',
                    step.status === 'completed' ? 'bg-success-300' : 'bg-neutral-200'
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
                      step.status === 'completed' && 'text-neutral-900',
                      step.status === 'current' && 'text-primary-700',
                      step.status === 'pending' && 'text-neutral-400',
                      step.status === 'skipped' && 'text-neutral-400 line-through'
                    )}
                  >
                    {step.actorName ?? step.role}
                  </p>

                  {step.status === 'completed' && step.completedAt && (
                    <p className="text-xs text-neutral-400 mt-0.5">{formatDateTime(step.completedAt)}</p>
                  )}

                  {step.status === 'current' && (
                    <div className="mt-0.5">
                      <p className="text-xs text-primary-600">Awaiting action</p>
                      {step.daysWaiting != null && (
                        <p className="text-xs text-neutral-400">
                          Waiting {step.daysWaiting} day{step.daysWaiting !== 1 ? 's' : ''}
                        </p>
                      )}
                    </div>
                  )}

                  {step.status === 'pending' && (
                    <p className="text-xs text-neutral-400 mt-0.5">{step.role}</p>
                  )}
                </div>

                <div className="flex items-center gap-1.5 shrink-0">
                  {step.channel && (
                    <span className={cn('rounded-full px-1.5 py-0.5 text-[10px] font-medium', CHANNEL_BADGES[step.channel])}>
                      {step.channel}
                    </span>
                  )}
                  {step.action && (
                    <span
                      className={cn(
                        'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                        step.action === 'Approved' && 'bg-success-50 text-success-700',
                        step.action === 'Returned' && 'bg-warning-50 text-warning-700',
                        step.action === 'Rejected' && 'bg-danger-50 text-danger-700'
                      )}
                    >
                      {step.action}
                    </span>
                  )}
                </div>
              </div>

              {step.comment && (
                <p className="mt-1 text-xs text-neutral-500 italic">"{step.comment}"</p>
              )}

              {step.status === 'current' && onSendReminder && (
                <button
                  onClick={() => onSendReminder(step.id)}
                  className="mt-1.5 text-xs text-primary-600 hover:underline"
                >
                  Send reminder
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
