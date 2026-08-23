import { useState } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useResolveException } from '@/hooks/useInvoices'
import type { ApiInvoice } from '@/services/invoices'

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
    <div className="mt-2 rounded-xl border border-warning-200 bg-warning-50 p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-warning-700">Resolve Exception</p>
        <button onClick={onClose} className="text-neutral-400 hover:text-neutral-600">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="rounded-lg border border-warning-200 bg-white p-3 text-xs">
        <p className="font-medium text-neutral-700 mb-1">Variance Details</p>
        <p className="text-neutral-500">{inv.exception_reason}</p>
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
              : 'border-neutral-200 text-neutral-500 hover:border-neutral-300'
          )}>
            <input type="radio" name="resolution" value={opt.value} checked={resolution === opt.value}
              onChange={() => setResolution(opt.value)} className="h-3 w-3" />
            {opt.label}
          </label>
        ))}
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-700">
          {resolution === 'accepted' ? 'Justification' : 'Instructions'} <span className="text-danger-600">*</span>
        </label>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={resolution === 'accepted'
            ? 'Explain why the excess amount is acceptable...'
            : 'Instructions for the vendor to issue a credit note...'}
          className={cn(
            'px-3 py-2 rounded-lg border text-xs focus:outline-none focus:ring-1 focus:ring-primary-600 resize-none',
            submitted && !note.trim() ? 'border-danger-400' : 'border-neutral-300 bg-white'
          )} />
        {submitted && !note.trim() && <p className="text-xs text-danger-600">Required</p>}
      </div>

      {resolveExceptionMutation.isError && (
        <p className="text-xs text-danger-600 text-right">
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
