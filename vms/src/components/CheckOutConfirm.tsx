/** Check-out confirmation modal (PRD §2.4.2 VMS-CO-005..007).
 *
 * Shows what we matched on the QR + collects badge_returned, ppe_returned,
 * and optional notes. Caller wires the actual mutation.
 */
import { useState } from 'react'
import { CheckCircle2, Loader2, X } from 'lucide-react'
import type { Visit, Visitor, UserBrief, CheckOutPayload } from '@/services/api'
import { StatusBadge, AccessAreaBadge } from '@/components/StatusBadge'
import { formatDateTime } from '@/lib/utils'

interface Props {
  visit: Visit
  visitor: Visitor | undefined
  host: UserBrief | undefined
  onConfirm: (payload: CheckOutPayload) => void
  onCancel: () => void
  isPending: boolean
  error: Error | null
}

export function CheckOutConfirm({
  visit, visitor, host, onConfirm, onCancel, isPending, error,
}: Props) {
  const [badgeReturned, setBadge] = useState(true)
  const [ppeReturned, setPpe]     = useState(true)
  const [notes, setNotes]         = useState('')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-lg bg-white shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between border-b border-neutral-100 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-neutral-900">
              Check out visitor
            </h3>
            <p className="mt-0.5 text-xs text-neutral-500">
              Confirm departure and badge return.
            </p>
          </div>
          <button
            onClick={onCancel}
            className="rounded-md p-1 text-neutral-400 hover:bg-neutral-50 hover:text-neutral-600"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Visit summary */}
        <div className="space-y-2 border-b border-neutral-100 px-5 py-3 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="text-neutral-500">Visitor</span>
            <span className="font-medium text-neutral-900">
              {visitor ? `${visitor.first_name} ${visitor.last_name}` : '—'}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-neutral-500">Company</span>
            <span className="text-neutral-700">{visitor?.company_name ?? '—'}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-neutral-500">Host</span>
            <span className="text-neutral-700">{host?.full_name ?? '—'}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-neutral-500">Checked in</span>
            <span className="text-neutral-700">{formatDateTime(visit.actual_arrival)}</span>
          </div>
          <div className="flex items-center gap-2 pt-1">
            <StatusBadge status={visit.status} />
            <AccessAreaBadge area={visit.access_area} />
          </div>
        </div>

        {/* Form */}
        <div className="space-y-3 px-5 py-4">
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={badgeReturned}
              onChange={(e) => setBadge(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-neutral-800">Badge returned</span>
              <span className="block text-xs text-neutral-500">
                Visitor handed back their printed badge.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={ppeReturned}
              onChange={(e) => setPpe(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-neutral-800">PPE returned</span>
              <span className="block text-xs text-neutral-500">
                Hard hat, protective clothing, shoe covers, etc.
              </span>
            </span>
          </label>
          <label className="block text-sm">
            <span className="font-medium text-neutral-700">Notes (optional)</span>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="Anything to record about the departure…"
              className="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
            />
          </label>

          {error && (
            <p className="rounded-md bg-danger-50 px-2.5 py-1.5 text-xs text-danger-600">
              {error.message}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2 border-t border-neutral-100 px-5 py-3">
          <button
            onClick={onCancel}
            disabled={isPending}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm({
              badge_returned: badgeReturned,
              ppe_returned:   ppeReturned,
              notes: notes.trim() || null,
            })}
            disabled={isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {isPending
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <CheckCircle2 className="h-4 w-4" />}
            Confirm departure
          </button>
        </div>
      </div>
    </div>
  )
}
