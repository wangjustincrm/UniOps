import { useRef, useState } from 'react'
import { Button } from '../primitives/shell'
import { Dialog } from '../primitives/Dialog'
import { errorText } from '../resource/businessError'

export interface ConfirmActionProps {
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

export function ConfirmAction({
  open, onOpenChange, title, description, confirmLabel = 'Confirm', destructive, onConfirm, errorMessages,
}: ConfirmActionProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The ref, not the state: two clicks in one tick both see pending=false.
  const inFlight = useRef(false)

  const confirm = async () => {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError(null)
    try {
      await onConfirm()
      onOpenChange(false)
    } catch (e) {
      setError(errorText(e, errorMessages))
    } finally {
      inFlight.current = false
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
          <Button type="button" variant="secondary" disabled={pending} onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="button" variant={destructive ? 'destructive' : 'primary'} disabled={pending} onClick={confirm}>
            {pending ? 'Working…' : confirmLabel}
          </Button>
        </>
      }
    >
      {error && <p role="alert" className="text-sm text-danger-600">{error}</p>}
    </Dialog>
  )
}
