/**
 * Confirmation for reversing a match, from the 3-Way Match tab.
 *
 * The reason is mandatory and is not decoration: the backend stores it on an
 * admin_audit_log row alongside the PO/GR/allocation snapshot it tore down.
 * Unmatching is a financial action — someone has to be able to ask later why
 * a verified evidence chain was taken apart.
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Unlink, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useUnmatchInvoice, type UnmatchScope } from '@/hooks/useInvoices'

import { Textarea } from '@uniops/shell'
const COPY: Record<UnmatchScope, { title: string; body: string; confirm: string }> = {
  po: {
    title: 'Unmatch Purchase Order',
    body: 'This returns the invoice to Unmatched: its PO allocations are deleted and the '
        + 'goods receipt link goes with the PO it belongs to. The invoice can then be '
        + 'matched again from the Unmatched Queue.',
    confirm: 'Unmatch PO',
  },
  gr: {
    title: 'Unmatch Goods Receipt',
    body: 'This withdraws only the receipt evidence. The invoice stays matched to its PO '
        + 'and keeps its allocations.',
    confirm: 'Unmatch GR',
  },
  agreement: {
    title: 'Unmatch Agreement',
    body: 'This returns the invoice to Unmatched: the billing period, milestone stage or '
        + 'receipts it claimed go back to the agreement, and the agreement no longer counts '
        + 'it as spend. The invoice can then be matched again from the Unmatched Queue. '
        + 'Not possible while a payment application still lists it.',
    confirm: 'Unmatch Agreement',
  },
  direct: {
    title: 'Take Off the Direct PA Route',
    body: 'This returns the invoice to Unmatched, so it can be matched to a PO or agreement '
        + 'instead. Not possible while a payment application still lists it.',
    confirm: 'Unmatch',
  },
}

export function UnmatchDialog({
  invoiceId,
  scope,
  onClose,
}: {
  invoiceId: string
  scope: UnmatchScope
  onClose: () => void
}) {
  const [reason, setReason] = useState('')
  const unmatch = useUnmatchInvoice()
  const copy = COPY[scope]
  const ready = reason.trim().length > 0

  const submit = () => {
    // Second half of the double gate: `disabled` alone still lets a fast
    // double-click through between the click and the re-render.
    if (!ready || unmatch.isPending) return
    unmatch.mutate({ id: invoiceId, scope, reason: reason.trim() }, { onSuccess: onClose })
  }

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-inverse/40 p-4 backdrop-blur-sm">
      <div className="flex w-full max-w-md flex-col rounded-2xl bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3.5">
          <div className="flex items-center gap-2">
            <Unlink className="h-4 w-4 text-danger-fg" />
            <h2 className="text-sm font-semibold text-fg">{copy.title}</h2>
          </div>
          <Button variant="subtle" size="icon-sm" aria-label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex flex-col gap-3 px-5 py-4">
          <p className="text-xs text-fg-secondary">{copy.body}</p>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-fg-secondary">Reason (required)</span>
            <Textarea
              autoFocus
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this being unmatched?"
              className="w-full"
            />
          </label>
          {unmatch.isError && (
            <p className="text-xs text-danger-fg">
              {unmatch.error instanceof Error ? unmatch.error.message : 'Unmatch failed'}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-line-subtle px-5 py-3.5">
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button variant="destructive" size="sm" disabled={!ready || unmatch.isPending} onClick={submit}>
            {unmatch.isPending ? 'Unmatching…' : copy.confirm}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  )
}
