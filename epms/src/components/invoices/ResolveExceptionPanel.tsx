import { useState } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useResolveException } from '@/hooks/useInvoices'
import type { ApiInvoice } from '@/services/invoices'

import { IconButton, Radio, Textarea } from '@uniops/shell'
// Shared by InvoiceListPage's Exceptions tab and InvoiceDetailPage — lifted
// out (whole-branch review finding 2) so the resolve_exception task's deep
// link (/invoices/{id}, the detail page) lands somewhere that can actually
// resolve it, without duplicating the mutation logic.
export function ResolveExceptionPanel({ inv, onClose }: { inv: ApiInvoice; onClose: () => void }) {
  const resolveExceptionMutation = useResolveException()
  const [resolution, setResolution] = useState<'accepted' | 'credit_note_requested'>('accepted')
  const [note, setNote] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const handleResolve = () => {
    setSubmitted(true)
    if (!note.trim()) return
    resolveExceptionMutation.mutate(
      { id: inv.id, resolution, note: note.trim() },
      {
        onSuccess: onClose,
        onError: (err) => {
          // error displayed below the button
          console.error('resolve exception failed:', err)
        },
      }
    )
  }

  return (
    <div className="mt-2 rounded-xl border border-warning-line bg-warning-soft p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-warning-fg">Resolve Exception</p>
        <IconButton label="Close" icon={<X className="h-4 w-4" />} size="icon-xs" onClick={onClose} />
      </div>

      <div className="rounded-lg border border-warning-line bg-surface p-3 text-xs">
        <p className="font-medium text-fg-secondary mb-1">Variance Details</p>
        <p className="text-fg-muted">{inv.exception_reason}</p>
      </div>

      <div className="flex gap-3">
        {([
          { value: 'accepted',               label: 'Accept with Justification' },
          { value: 'credit_note_requested',  label: 'Request Credit Note'       },
        ] as const).map((opt) => (
          <label key={opt.value} className={cn(
            'flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer text-xs transition-colors',
            resolution === opt.value
              ? 'border-primary-400 bg-primary-50 text-primary-700 font-medium'
              : 'border-line text-fg-muted hover:border-line-strong'
          )}>
            <Radio name="resolution" value={opt.value} checked={resolution === opt.value}
              onChange={() => setResolution(opt.value)} />
            {opt.label}
          </label>
        ))}
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-fg-secondary">
          {resolution === 'accepted' ? 'Justification' : 'Instructions'} <span className="text-danger-fg">*</span>
        </label>
        <Textarea rows={2} maxLength={4000} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={resolution === 'accepted'
            ? 'Explain why the excess amount is acceptable...'
            : 'Instructions for the vendor to issue a credit note...'}
          error={!!(submitted && !note.trim())} className="resize-none text-xs" />
        {submitted && !note.trim() && <p className="text-xs text-danger-fg">Required</p>}
      </div>

      {resolution === 'credit_note_requested' && (
        <p className="text-xs text-fg-muted">
          The invoice stays in Exception and the task stays open as "Awaiting credit note" until
          the credit arrives and you accept or edit the invoice.
        </p>
      )}

      {resolveExceptionMutation.isError && (
        <p className="text-xs text-danger-fg text-right">
          {resolveExceptionMutation.error instanceof Error
            ? resolveExceptionMutation.error.message
            : 'Failed to resolve exception'}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={resolveExceptionMutation.isPending}>Cancel</Button>
        <Button size="sm" onClick={handleResolve} disabled={resolveExceptionMutation.isPending}>
          {resolveExceptionMutation.isPending ? 'Submitting…' : 'Submit Resolution'}
        </Button>
      </div>
    </div>
  )
}
