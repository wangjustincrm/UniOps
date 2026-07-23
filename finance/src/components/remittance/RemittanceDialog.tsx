/**
 * Modal wrapper around RemittancePanel — pop it up right after a payment or
 * batch execute completes so the operator can send remittance advice without
 * leaving the flow. Follows the app's modal convention (see
 * PaymentBatchPage.tsx's BatchDetailModal / JvDetailModal.tsx): fixed
 * backdrop, click-outside-to-close, `stopPropagation` on the inner panel.
 */
import { X } from 'lucide-react'
import { RemittancePanel } from './RemittancePanel'
import type { RemittanceScope, SendResult } from '@/services/remittance'

const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

export function RemittanceDialog({ scope, open, onClose, onSent }: {
  scope: RemittanceScope
  open: boolean
  onClose: () => void
  onSent?: (result: SendResult) => void
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold text-neutral-800">Send Remittance Advice</h2>
            <p className="text-sm text-neutral-500">Review the recipients below and send.</p>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        <RemittancePanel scope={scope} onSent={onSent} />

        <div className="mt-4 flex justify-end">
          <button type="button" onClick={onClose} className={secondaryBtn}>Close</button>
        </div>
      </div>
    </div>
  )
}
