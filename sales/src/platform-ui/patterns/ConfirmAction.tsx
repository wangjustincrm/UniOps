import { useId, useRef, useState } from 'react'
import { Button, FormField } from '../primitives/shell'
import { Textarea } from '../primitives/FieldInputs'
import { Dialog } from '../primitives/Dialog'
import { errorText } from '../resource/businessError'

export interface ConfirmActionProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel?: string
  destructive?: boolean
  /** May be async; the dialog stays open, buttons disabled, until it settles.
   *  With `reason`, it receives the text typed (trimmed). */
  onConfirm: (reason: string) => unknown | Promise<unknown>
  errorMessages?: Record<string, string>
  /** Asks why: a text the person must fill (at least `minLength`, default 3)
   *  before Confirm is enabled — e.g. cancelling, releasing a hold. */
  reason?: { label: string; minLength?: number }
}

export function ConfirmAction({
  open, onOpenChange, title, description, confirmLabel = 'Confirm', destructive, onConfirm, errorMessages, reason,
}: ConfirmActionProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const reasonId = useId()
  const missing = !!reason && text.trim().length < (reason.minLength ?? 3)
  // The ref, not the state: two clicks in one tick both see pending=false.
  const inFlight = useRef(false)

  const confirm = async () => {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError(null)
    try {
      await onConfirm(text.trim())
      setText('')
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
          <Button type="button" variant={destructive ? 'destructive' : 'primary'} disabled={pending || missing} onClick={confirm}>
            {pending ? 'Working…' : confirmLabel}
          </Button>
        </>
      }
    >
      {reason && (
        <FormField label={reason.label} htmlFor={reasonId} required>
          <Textarea id={reasonId} rows={2} value={text} disabled={pending} onChange={(e) => setText(e.target.value)} />
        </FormField>
      )}
      {error && <p role="alert" className="text-sm text-danger-600">{error}</p>}
    </Dialog>
  )
}
