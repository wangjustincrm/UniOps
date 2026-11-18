/** Check-out confirmation modal (PRD §2.4.2 VMS-CO-005..007).
 *
 * Shows what we matched on the QR + collects badge_returned, ppe_returned,
 * and optional notes. Caller wires the actual mutation.
 */
import { useState } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'
import type { Visit, Visitor, UserBrief, CheckOutPayload } from '@/services/api'
import { StatusBadge, AccessAreaBadge } from '@/components/StatusBadge'
import { formatDateTime } from '@/lib/utils'

import { Checkbox, Textarea, Button, Modal } from '@uniops/shell'
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
    <Modal
      open
      onClose={onCancel}
      title="Check out visitor"
      description="Confirm departure and badge return."
      // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
      closeOnEscape={false}
      size="full"
      className="max-w-md"
      bodyClassName="p-0"
    >
        {/* Visit summary */}
        <div className="space-y-2 border-b border-line-subtle px-5 py-3 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="text-fg-muted">Visitor</span>
            <span className="font-medium text-fg">
              {visitor ? `${visitor.first_name} ${visitor.last_name}` : '—'}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-fg-muted">Company</span>
            <span className="text-fg-secondary">{visitor?.company_name ?? '—'}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-fg-muted">Host</span>
            <span className="text-fg-secondary">{host?.full_name ?? '—'}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-fg-muted">Checked in</span>
            <span className="text-fg-secondary">{formatDateTime(visit.actual_arrival)}</span>
          </div>
          <div className="flex items-center gap-2 pt-1">
            <StatusBadge status={visit.status} />
            <AccessAreaBadge area={visit.access_area} />
          </div>
        </div>

        {/* Form */}
        <div className="space-y-3 px-5 py-4">
          <label className="flex items-start gap-2 text-sm">
            <Checkbox
             
              checked={badgeReturned}
              onChange={(e) => setBadge(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-fg">Badge returned</span>
              <span className="block text-xs text-fg-muted">
                Visitor handed back their printed badge.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <Checkbox
             
              checked={ppeReturned}
              onChange={(e) => setPpe(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-fg">PPE returned</span>
              <span className="block text-xs text-fg-muted">
                Hard hat, protective clothing, shoe covers, etc.
              </span>
            </span>
          </label>
          <label className="block text-sm">
            <span className="font-medium text-fg-secondary">Notes (optional)</span>
            <Textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="Anything to record about the departure…"
              className="mt-1 w-full"
            />
          </label>

          {error && (
            <p className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger-fg">
              {error.message}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2 border-t border-line-subtle px-5 py-3">
          <Button variant="secondary" size="sm"
            onClick={onCancel}
            disabled={isPending}
           
          >
            Cancel
          </Button>
          <Button size="sm"
            onClick={() => onConfirm({
              badge_returned: badgeReturned,
              ppe_returned:   ppeReturned,
              notes: notes.trim() || null,
            })}
            disabled={isPending}
           
          >
            {isPending
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <CheckCircle2 className="h-4 w-4" />}
            Confirm departure
          </Button>
        </div>
    </Modal>
  )
}
