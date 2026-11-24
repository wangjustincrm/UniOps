import { useId, useRef, useState } from 'react'
import { Button, FormField } from '../primitives/shell'
import { Textarea } from '../primitives/FieldInputs'
import { AlertDialog } from '../primitives/AlertDialog'
import { Dialog } from '../primitives/Dialog'
import { Hint } from '../primitives/Text'
import { errorText, isStale } from '../resource/businessError'
import { DisabledReason } from '../help'
import { useAfterAction, useReadAgain, type ActionOptions } from './useAction'
import { compareRecord } from './useRefresh'
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
  /** The record it acts on, as the page has it now. When the server says it moved (entity.stale) the dialog stays, what was
   *  typed stays, the record is read again, and the dialog says which fields changed; the next press acts on the new one. */
  record?: unknown
  /** What it sends out of the company (useActionSends): a line of the dialog. */
  sends?: string
}

export function ConfirmAction({
  open, onOpenChange, title, description, confirmLabel = 'Confirm', destructive, onConfirm, errorMessages, reason,
  done, refreshes, invalidates, after, toStatusRow, record, sends,
}: ConfirmActionProps) {
  const afterAction = useAfterAction()
  const readAgain = useReadAgain()
  const [seen, setSeen] = useState<unknown>(null)  // the record as it was when the server said it had moved
  const changes = seen && record && record !== seen ? compareRecord(seen, record).text : null
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
      setSeen(null)
      onOpenChange(false)
      await afterAction(result, { done, refreshes, invalidates, after })
    } catch (e) {
      if (toStatusRow?.(e)) {
        setText('')
        onOpenChange(false)
        notices.notify({ tone: 'error', text: errorText(e, errorMessages) })
        return
      }
      if (isStale(e)) setSeen(record)
      readAgain(e, invalidates)
      setError(errorText(e, errorMessages))
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  const change = (o: boolean) => { if (!pending) { setError(null); setSeen(null); onOpenChange(o) } }
  const footer = (
        <>
          <Button type="button" variant="secondary" disabled={pending} onClick={() => change(false)}>Cancel</Button>
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
  )
  const body = (
    <>
      {sends && <Hint as="p">Sends {sends}.</Hint>}
      {reason && (
        <FormField label={reason.label} htmlFor={reasonId} required>
          <Textarea id={reasonId} rows={2} value={text} disabled={pending} onChange={(e) => setText(e.target.value)} />
        </FormField>
      )}
      {error && <p role="alert" className="text-sm text-danger-700">{error}{changes ? ` ${changes}.` : ''}</p>}
    </>
  )
  // A destructive confirm interrupts (shadcn's AlertDialog: role="alertdialog", no outside-click close); others are a Dialog.
  if (destructive) {
    return (
      <AlertDialog open={open} onOpenChange={change} title={title} description={description}>
        {body}
        <div className="mt-2 flex justify-end gap-2">{footer}</div>
      </AlertDialog>
    )
  }
  return <Dialog open={open} onOpenChange={change} title={title} description={description} footer={footer}>{body}</Dialog>
}
