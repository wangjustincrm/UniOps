import { useState } from 'react'
import { Button } from '@uniops/shell'
import { Dialog } from './Dialog'
import { errorText } from '../lib/businessError'

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel?: string
  destructive?: boolean
  /** May be async; the dialog stays open, buttons disabled, until it settles. */
  onConfirm: () => unknown | Promise<unknown>
  errorMessages?: Record<string, string>
}

export function ConfirmDialog({
  open, onOpenChange, title, description, confirmLabel = 'Confirm', destructive, onConfirm, errorMessages,
}: ConfirmDialogProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const confirm = async () => {
    if (pending) return
    setPending(true)
    setError(null)
    try {
      await onConfirm()
      onOpenChange(false)
    } catch (e) {
      setError(errorText(e, errorMessages))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => { if (!pending) { setError(null); onOpenChange(o) } }}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="secondary" disabled={pending} onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant={destructive ? 'destructive' : 'primary'} disabled={pending} onClick={confirm}>
            {pending ? 'Working…' : confirmLabel}
          </Button>
        </>
      }
    >
      {error && <p role="alert" className="text-sm text-danger-600">{error}</p>}
    </Dialog>
  )
}
