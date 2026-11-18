// "Add intent product" — creates a placeholder planned SKU (no ERP material
// code yet) that Sales Forecast can immediately start recording numbers
// against under its own INTENT-xxxxxxxx code (see intentApi.ts's header).
// Same portal-modal shape as GenerateOutlookModal.tsx; the submit/error/
// notify split (self-contained POST, `notifySuccess`/`notifyError` owned by
// the caller) matches AdjustDrawer.tsx (mps/AdjustDrawer.tsx) — this repo's
// convention for a small create/update form that doesn't need its own page.
import { useState, type FormEvent } from 'react'
import { AlertTriangle, Lightbulb, Loader2 } from 'lucide-react'
import { Button, Input, FormField, Textarea, Modal } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { intentApi, type IntentProduct } from './intentApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function AddIntentProductModal({
  onClose, onCreated, notifySuccess, notifyError,
}: {
  onClose: () => void
  /** Called after a successful create, before onClose — lets the page seed
   *  the new code into the grid as a session row (same mechanic "Add
   *  Product" uses) and warm the intent-products cache. */
  onCreated: (created: IntentProduct) => void
  notifySuccess: (message: string) => void
  notifyError: (message: string) => void
}) {
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [nameError, setNameError] = useState<string | undefined>(undefined)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  function validateName(v: string): string | undefined {
    return v.trim() ? undefined : 'Enter a name.'
  }

  function handleNameBlur() {
    setNameError(validateName(name))
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const err = validateName(name)
    setNameError(err)
    if (err) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      const created = await intentApi.create(name.trim(), note.trim() || undefined)
      notifySuccess(`Created intent product ${created.code} — ${created.name}.`)
      onCreated(created)
      onClose()
    } catch (err) {
      const msg = errMsg(err, 'Could not create this intent product — please retry.')
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Add Intent Product"
      icon={<Lightbulb className="h-4 w-4 text-warning-fg" />}
      closeOnEscape={false}
      size="full"
      className="max-w-md"
      bodyClassName="p-0"
    >
        <form onSubmit={handleSubmit}>
          <div className="px-5 py-4 space-y-4">
            <p className="text-sm text-fg-secondary">
              A planned SKU with no ERP material code yet. It gets a placeholder code and can be
              forecast right away — bind it to a real material code once one exists, and its numbers
              move over automatically.
            </p>

            <FormField label="Name" required htmlFor="intent-name" error={nameError}>
              <Input
                id="intent-name"
                value={name}
                onChange={(e) => { setName(e.target.value); if (nameError) setNameError(undefined) }}
                onBlur={handleNameBlur}
                disabled={submitting}
                error={!!nameError}
                placeholder="e.g. New A2 Stage 3 (working name)"
              />
            </FormField>

            <FormField label="Note" htmlFor="intent-note" hint="Optional — context for whoever binds this later.">
              <Textarea
                id="intent-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                disabled={submitting}
                rows={3}
                className="w-full"
              />
            </FormField>

            {submitError && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2 px-5 py-4 border-t border-line">
            <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={submitting}>Cancel</Button>
            <Button type="submit" size="sm" disabled={submitting}>
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Create
            </Button>
          </div>
        </form>
    </Modal>
  )
}
