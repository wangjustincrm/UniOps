import { AlertTriangle } from 'lucide-react'

import { Alert, Button, Modal } from '@uniops/shell'
/** Destructive-action confirmation. Same dialog as ActionModal, minus the
 *  comment box — a hard delete records no reason anywhere. */
export function ConfirmDialog({
  title, message, confirmLabel = 'Delete', onConfirm, onClose, loading, error,
}: {
  title: string
  message: string
  confirmLabel?: string
  onConfirm: () => void
  onClose: () => void
  loading: boolean
  error?: string
}) {
  // Closes only by Cancel / the close button, as before (no Escape, no backdrop).
  return (
    <Modal
      open
      onClose={onClose}
      closeOnEscape={false}
      title={title}
      icon={<AlertTriangle className="h-4 w-4 text-danger-fg" />}
      footer={<>
        <Button variant="secondary" size="md" onClick={onClose}>Cancel</Button>
        <Button variant="danger" size="md" onClick={onConfirm} disabled={loading}>
          {loading ? 'Deleting…' : confirmLabel}
        </Button>
      </>}
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-fg-secondary">{message}</p>
        {error && <Alert tone="danger">{error}</Alert>}
      </div>
    </Modal>
  )
}
