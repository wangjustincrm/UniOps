// "Add intent product" — creates a placeholder planned SKU (no ERP material
// code yet) that Sales Forecast can immediately start recording numbers
// against under its own INTENT-xxxxxxxx code (see intentApi.ts's header).
// Same portal-modal shape as GenerateOutlookModal.tsx; the submit/error/
// notify split (self-contained POST, `notifySuccess`/`notifyError` owned by
// the caller) matches AdjustDrawer.tsx (mps/AdjustDrawer.tsx) — this repo's
// convention for a small create/update form that doesn't need its own page.
import { useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Lightbulb, Loader2, X as XIcon } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'
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

  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-label="Add intent product" className="w-full max-w-md rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-neutral-200">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-900">
            <Lightbulb className="h-4 w-4 text-warning-600" /> Add Intent Product
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-m-2.5 flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-neutral-600"
          >
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="px-5 py-4 space-y-4">
            <p className="text-sm text-neutral-600">
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
              <textarea
                id="intent-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                disabled={submitting}
                rows={3}
                className="w-full rounded-lg border border-neutral-200 bg-neutral-100 px-3 py-2 text-sm text-neutral-900 placeholder:text-neutral-400 transition-colors focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)] disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400"
              />
            </FormField>

            {submitError && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2 px-5 py-4 border-t border-neutral-200">
            <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={submitting}>Cancel</Button>
            <Button type="submit" size="sm" disabled={submitting}>
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Create
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
