import { useId, useRef, useState } from 'react'
import { Button, FormField } from '../primitives/shell'
import { Textarea } from '../primitives/FieldInputs'
import { Dialog } from '../primitives/Dialog'
import { errorText } from '../resource/businessError'
import { DisabledReason } from '../help'
import { useAfterAction, type ActionOptions } from './useAction'
import { useNotices } from './notices'

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
  /** After it worked, as an action button: what to say (the status row), what to read again (default
   *  everything on screen; `refreshes={false}` nothing), and what then (`after`). A failure stays in the dialog. */
  done?: ActionOptions['done']
  refreshes?: boolean
  invalidates?: ActionOptions['invalidates']
  after?: ActionOptions['after']
  /** Failures that end the dialog and are said in the status row instead of in it (e.g. a refusal that no
   *  change to the dialog's input can fix). Anything else stays in the dialog. */
  toStatusRow?: (error: unknown) => boolean
}

export function ConfirmAction({
  open, onOpenChange, title, description, confirmLabel = 'Confirm', destructive, onConfirm, errorMessages, reason,
  done, refreshes, invalidates, after, toStatusRow,
}: ConfirmActionProps) {
  const afterAction = useAfterAction()
  const notices = useNotices()
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
      const result = await onConfirm(text.trim())
      setText('')
      onOpenChange(false)
      await afterAction(result, { done, refreshes, invalidates, after })
    } catch (e) {
      if (toStatusRow?.(e)) {
        setText('')
        onOpenChange(false)
        notices.notify({ tone: 'error', text: errorText(e, errorMessages) })
        return
      }
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
          {missing && !pending ? (
            <DisabledReason reason={`Fill in "${reason!.label}" first (at least ${reason!.minLength ?? 3} characters).`}>
              <Button type="button" variant={destructive ? 'destructive' : 'primary'} className="cursor-not-allowed opacity-50">{confirmLabel}</Button>
            </DisabledReason>
          ) : (
            <Button type="button" variant={destructive ? 'destructive' : 'primary'} disabled={pending} onClick={confirm}>
              {pending ? 'Working…' : confirmLabel}
            </Button>
          )}
        </>
      }
    >
      {reason && (
        <FormField label={reason.label} htmlFor={reasonId} required>
          <Textarea id={reasonId} rows={2} value={text} disabled={pending} onChange={(e) => setText(e.target.value)} />
        </FormField>
      )}
      {error && <p role="alert" className="text-sm text-danger-700">{error}</p>}
    </Dialog>
  )
}
