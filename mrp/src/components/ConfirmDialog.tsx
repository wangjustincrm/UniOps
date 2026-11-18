// Generic confirm dialog (Shell Modal). Used for "Confirm version"
// (summarise what's being locked) and the import wizard's apply step.
import { AlertTriangle, Loader2 } from 'lucide-react'
import { Button, Modal } from '@uniops/shell'
import { cn } from '@/lib/utils'

export interface ConfirmDialogProps {
  title: string
  children: React.ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
  danger?: boolean
}

export function ConfirmDialog({
  title, children, confirmLabel, onConfirm, onCancel, busy = false, danger = false,
}: ConfirmDialogProps) {
  // `stacked`: it can ask from inside a drawer. Closes by Cancel / the close
  // button only (no Escape, no backdrop), as before.
  return (
    <Modal
      open
      stacked
      onClose={onCancel}
      title={title}
      icon={danger ? <AlertTriangle className="h-4 w-4 text-warning-fg" /> : undefined}
      closeOnEscape={false}
      size="full"
      className="max-w-md"
      footer={<>
        <Button type="button" variant="secondary" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          type="button"
          size="sm"
          variant={danger ? 'destructive' : 'primary'}
          onClick={onConfirm}
          disabled={busy}
          className={cn(busy && 'cursor-wait')}
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {confirmLabel}
        </Button>
      </>}
    >
      <div className="space-y-2 text-sm text-fg-secondary">
        {children}
      </div>
    </Modal>
  )
}
