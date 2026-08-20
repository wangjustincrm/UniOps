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
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-neutral-900/40 p-4 backdrop-blur-sm">
      <div className="flex w-full max-w-md flex-col rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-3.5">
          <div className="flex items-center gap-2">
            <Unlink className="h-4 w-4 text-danger-600" />
            <h2 className="text-sm font-semibold text-neutral-900">{copy.title}</h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-col gap-3 px-5 py-4">
          <p className="text-xs text-neutral-600">{copy.body}</p>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-neutral-700">Reason (required)</span>
            <textarea
              autoFocus
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this being unmatched?"
              className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
            />
          </label>
          {unmatch.isError && (
            <p className="text-xs text-danger-600">
              {unmatch.error instanceof Error ? unmatch.error.message : 'Unmatch failed'}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-neutral-100 px-5 py-3.5">
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
