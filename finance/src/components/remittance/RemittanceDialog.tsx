/**
 * Modal wrapper around RemittancePanel — pop it up right after a payment or
 * batch execute completes so the operator can send remittance advice without
 * leaving the flow. Follows the app's modal convention (see
 * PaymentBatchPage.tsx's BatchDetailModal / JvDetailModal.tsx): fixed
 * backdrop, click-outside-to-close, `stopPropagation` on the inner panel.
 *
 * This is the shared modal shell for Tasks 12/13: the default header ("Send
 * Remittance Advice") suits a standalone send flow, but a consumer that
 * already has document-specific context to show (e.g. the Payments hub's
 * detail drawer, which needs the payment's doc number / payee / amount) can
 * override it via `header` instead of copying the backdrop/panel/X-button
 * shell a second time.
 */
import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { RemittancePanel } from './RemittancePanel'
import { secondaryBtn } from './buttonStyles'
import type { RemittanceScope, SendResult } from '@/services/remittance'

const DEFAULT_HEADER = (
  <div>
    <h2 className="text-base font-semibold text-neutral-800">Send Remittance Advice</h2>
    <p className="text-sm text-neutral-500">Choose the recipients, preview the emails, then confirm to send.</p>
  </div>
)

export function RemittanceDialog({ scope, open, onClose, onSent, header }: {
  scope: RemittanceScope
  open: boolean
  onClose: () => void
  onSent?: (result: SendResult) => void
  /** Replaces the default "Send Remittance Advice" title/subtitle block —
   * for consumers that need to show document-specific context (doc number,
   * payee, amount, status) alongside the send flow. */
  header?: ReactNode
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between">
          {header ?? DEFAULT_HEADER}
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
