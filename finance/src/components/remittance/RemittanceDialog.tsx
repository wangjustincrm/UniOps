/**
 * Modal wrapper around RemittancePanel — pop it up right after a payment or
 * batch execute completes so the operator can send remittance advice without
 * leaving the flow. Shell Modal; backdrop click closes, Escape does not (as
 * before).
 *
 * The default title ("Send Remittance Advice") suits a standalone send flow; a
 * consumer that already has document-specific context to show (e.g. the
 * Payments hub's detail view: doc number / payee / amount) passes its own
 * `title` and `description` instead of copying the dialog a second time.
 */
import type { ReactNode } from 'react'
import { RemittancePanel } from './RemittancePanel'
import type { RemittanceScope, SendResult } from '@/services/remittance'

import { Button, Modal } from '@uniops/shell'

export function RemittanceDialog({ scope, open, onClose, onSent, title, description }: {
  scope: RemittanceScope
  open: boolean
  onClose: () => void
  onSent?: (result: SendResult) => void
  title?: ReactNode
  description?: ReactNode
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title ?? 'Send Remittance Advice'}
      description={title ? description : (description ?? 'Choose the recipients, preview the emails, then confirm to send.')}
      closeOnBackdrop
      closeOnEscape={false}
      size="full"
      className="max-w-3xl"
      footer={<Button variant="secondary" size="sm" onClick={onClose}>Close</Button>}
    >
      <RemittancePanel scope={scope} onSent={onSent} />
    </Modal>
  )
}
