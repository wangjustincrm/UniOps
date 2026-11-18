import { XCircle } from 'lucide-react'
import { Modal } from '@uniops/shell'
import { Button } from '@/components/ui/button'

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
  // Escape closes without rejecting, like Cancel (Shell Modal default).
  return (
    <Modal
      open
      onClose={onClose}
      size="sm"
      icon={<XCircle className="text-danger-fg" />}
      title="Reject Receipt"
      description={receiptLabel}
      footer={<>
        <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
        <Button variant="danger" size="sm" onClick={onConfirm} data-autofocus>
          <XCircle className="h-4 w-4" />
          Reject
        </Button>
      </>}
    >
      <p className="text-sm text-fg-secondary">
        This is final — a rejected receipt can never be approved, removed, or edited afterward.
        The only way to record this spend again is to enter a brand-new receipt.
      </p>
    </Modal>
  )
}
