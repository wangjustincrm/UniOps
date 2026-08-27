/**
 * Read-only sign-off summary for the Edit Detail page.
 *
 * Someone filling in buyer detail needs to see where the sign-off stands — and,
 * when it has not been raised yet, needs to be told where to raise it. Without
 * this the two halves of the job look unrelated: you finish the buyer detail
 * here and there is nothing on the page suggesting a sign-off exists at all.
 *
 * Deliberately read-only. Raising, signing, returning and adding to the thread
 * all live on the PO detail page, so there is exactly one place each of those
 * happens.
 */
import { PenLine, ArrowRight, Clock } from 'lucide-react'
import { BackLink } from '@/components/BackLink'
import { cn, formatDateTime } from '@/lib/utils'
import { usePoSignoff } from '@/hooks/usePos'
import { STATUS_LABELS, THREAD_LABELS } from './PoSignoffPanel'

export function PoSignoffSummary({ poId, source }: { poId: string; source: string | null }) {
  const enabled = source === 'nc'
  const { data: state } = usePoSignoff(poId, enabled)
  if (!enabled || !state) return null

  const status = STATUS_LABELS[state.status] ?? STATUS_LABELS.draft
  const notStarted = state.status === 'draft'

  return (
    <section className="space-y-3 rounded-lg border border-neutral-200 p-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-900">
          <PenLine className="h-4 w-4 text-primary-600" />
          Sign-off
        </h2>
        <span className={cn('rounded-md px-2.5 py-1 text-xs font-semibold', status.className)}>
          {status.label}
        </span>
      </div>

      <p className="text-sm text-neutral-500">
        {notStarted
          ? 'No sign-off has been raised for this PO yet. Save your changes here first, then raise it from the PO detail page — that is also where the purchase justification is written.'
          : 'Raised, signed and discussed on the PO detail page. Shown here so you can see where it stands while filling in buyer detail.'}
      </p>

      {state.steps.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {state.steps.map((step, i) => (
            <li key={step.id} className="flex items-center gap-2 text-sm">
              <span className="w-5 shrink-0 text-xs text-neutral-400">{i + 1}.</span>
              <span className="font-medium text-neutral-800">{step.label}</span>
              <span className="text-neutral-500">
                {step.signed_at
                  ? `— signed by ${step.signed_by_name}, ${formatDateTime(step.signed_at)}`
                  : step.holder_count === 0 ? '— no active holder' : '— not signed'}
              </span>
            </li>
          ))}
        </ul>
      )}

      {state.thread.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-neutral-100 pt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
            Justification &amp; notes
          </p>
          {state.thread.map((entry, i) => (
            <div key={`${entry.at}-${i}`} className="rounded-lg bg-neutral-50 px-3 py-2">
              <p className="flex items-center gap-1.5 text-xs text-neutral-500">
                <Clock className="h-3 w-3 shrink-0" />
                <span className="font-medium text-neutral-700">{entry.actor_name ?? 'Unknown'}</span>
                · {THREAD_LABELS[entry.action] ?? entry.action}
                · {formatDateTime(entry.at)}
              </p>
              {entry.comment && (
                <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-800">{entry.comment}</p>
              )}
            </div>
          ))}
        </div>
      )}

      <BackLink to={`/po/${poId}`}>
        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-primary-600 hover:text-primary-700">
          {notStarted ? 'Go to the PO to raise the sign-off' : 'Open the sign-off on the PO'}
          <ArrowRight className="h-3.5 w-3.5" />
        </span>
      </BackLink>
    </section>
  )
}
