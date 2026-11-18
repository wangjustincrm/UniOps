import { createPortal } from 'react-dom'
import { X, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useCloseOnEscape } from '@/hooks/useCloseOnEscape'

/**
 * In-page confirmation for an AP Reject of a pending_ap_review receipt — in
 * the same shape as the Agreement / PA Reject dialogs, instead of the
 * browser's native confirm() (ISS-0115). It asks no reason: POST
 * …/receipts/{id}/ap-review takes only the action and the receipt has no
 * column to keep one in.
 *
 * Reject has no way back: crud/agreement_receipt.py writes status "rejected",
 * which is RETIRED (neither editable nor voidable), and ap_review only accepts
 * FROM pending_ap_review.
 */
export function RejectReceiptDialog({ receiptLabel, onClose, onConfirm }: {
  receiptLabel: string
  onClose: () => void
  onConfirm: () => void
}) {
  // Escape closes without rejecting, like Cancel.
  useCloseOnEscape(true, onClose)
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-inverse/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-surface shadow-2xl" role="dialog" aria-modal="true">
        <div className="flex items-center justify-between border-b border-line-subtle px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-danger-soft">
              <XCircle className="h-5 w-5 text-danger-fg" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-fg">Reject Receipt</h2>
              <p className="text-xs text-fg-muted">{receiptLabel}</p>
            </div>
          </div>
          <Button variant="subtle" size="icon-sm" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <p className="text-sm text-fg-secondary">
            This is final — a rejected receipt can never be approved, removed, or edited afterward.
            The only way to record this spend again is to enter a brand-new receipt.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
            <Button variant="danger" size="sm"
              onClick={onConfirm}
              autoFocus
             
            >
              <XCircle className="h-4 w-4" />
              Reject
            </Button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
