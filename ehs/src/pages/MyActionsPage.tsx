/**
 * What is assigned to me — where most employees land.
 *
 * Card list rather than a table: this is read one-handed on a phone, and a
 * table hides the one thing that matters, which is how late something is.
 */
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { api } from '@/lib/api'
import { daysOverdue, formatDate } from '@/lib/formatDate'
import type { ActionListItem } from '@/lib/types'
import { StatusBadge } from '@/components/StatusBadge'

function Lateness({ dueDate }: { dueDate: string }) {
  const late = daysOverdue(dueDate)
  if (late === null) return null
  if (late > 0) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-danger-600 px-2.5 py-1 text-xs font-medium text-white tabular-nums">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        {late}d late
      </span>
    )
  }
  if (late === 0) {
    return (
      <span className="rounded-md bg-warning-50 px-2.5 py-1 text-xs font-medium text-warning-700">
        Due today
      </span>
    )
  }
  return (
    <span className="rounded-md bg-neutral-100 px-2.5 py-1 text-xs font-medium text-neutral-600 tabular-nums">
      Due {formatDate(dueDate)}
    </span>
  )
}

const ESCALATION_NOTE: Record<number, string> = {
  2: 'Escalated to your supervisor.',
  3: 'Escalated to the HSE Manager.',
}

export default function MyActionsPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['actions', 'mine'],
    queryFn: () => api.get<ActionListItem[]>('/api/v1/actions/mine'),
  })

  if (isLoading) {
    return <p className="p-4 text-sm text-neutral-500">Loading your actions…</p>
  }
  if (isError) {
    return (
      <p className="p-4 text-sm text-danger-700">
        {(error as Error).message}
      </p>
    )
  }

  const actions = data ?? []

  if (actions.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 p-10 text-center">
        <CheckCircle2 className="h-8 w-8 text-success-500" aria-hidden />
        <p className="text-sm font-medium text-neutral-700">Nothing is assigned to you.</p>
        <p className="text-sm text-neutral-500">
          Corrective actions raised from incidents and inspections appear here.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 p-4">
      {actions.map((a) => (
        <article
          key={a.id}
          className="rounded-xl border border-neutral-200 bg-white p-3.5"
        >
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-neutral-900">{a.title}</h2>
              <p className="mt-0.5 text-xs text-neutral-500">
                {a.action_no}
                {a.source_ref ? ` · from ${a.source_ref}` : ''}
              </p>
            </div>
            <Lateness dueDate={a.due_date} />
          </div>
          {ESCALATION_NOTE[a.escalation_level] && (
            <p className="mt-2.5 border-t border-neutral-100 pt-2.5 text-xs text-neutral-600">
              {ESCALATION_NOTE[a.escalation_level]}
            </p>
          )}
          <div className="mt-2.5">
            <StatusBadge status={a.status} />
          </div>
        </article>
      ))}
    </div>
  )
}
