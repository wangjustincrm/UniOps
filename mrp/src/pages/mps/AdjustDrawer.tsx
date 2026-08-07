// Adjust a single MPS line's qty/plan_month/lock — design §6.6 page 3.
// Portaled to document.body as a right-side drawer, same convention as
// capacity/RuleDrawer.tsx (see that file's header note and
// feedback_uniops_overlay_dropdown_portal in project memory).
//
// qty and plan_month were always editable here (brief: "edit qty or
// plan_month for one line"). Locking used to be a bulk action from
// MpsLineTable's own action bar (row checkboxes + Lock/Unlock selected) —
// Production Plan Matrix Task 5 retired that table in favor of
// ProductionMatrix.tsx's per-cell click-to-adjust, so locking moved into
// this single-line form as a `locked_by_planner` checkbox instead of being
// dropped.
import { useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Loader2, X as XIcon } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { mpsApi, type MpsLine } from './mpsApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

interface FormErrors {
  qty?: string
  plan_month?: string
}

function validate(qty: string, planMonth: string): FormErrors {
  const errs: FormErrors = {}
  const n = Number(qty)
  if (qty.trim() === '' || !Number.isFinite(n) || n < 0) errs.qty = 'Enter a quantity of 0 or more.'
  if (!planMonth) errs.plan_month = 'Select a plan month.'
  return errs
}

export function AdjustDrawer({
  runId, line, onClose, onSaved, notifySuccess, notifyError,
}: {
  runId: string
  line: MpsLine
  onClose: () => void
  /** Called after a successful PATCH so the page can refetch the run
   *  (qty/plan_month changes shift capacity occupancy too) — does not
   *  close the drawer itself, same split RuleDrawer.tsx uses. */
  onSaved: () => void
  notifySuccess: (message: string) => void
  notifyError: (message: string) => void
}) {
  const [qty, setQty] = useState(line.qty)
  const [planMonth, setPlanMonth] = useState(line.plan_month)
  const [locked, setLocked] = useState(line.locked_by_planner)
  const [errors, setErrors] = useState<FormErrors>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const errs = validate(qty, planMonth)
    setErrors(errs)
    if (Object.values(errs).some(Boolean)) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      await mpsApi.adjustLine(runId, line.id, { qty: Number(qty), plan_month: planMonth, locked_by_planner: locked })
      const lockNote = locked === line.locked_by_planner ? '' : locked ? ', locked' : ', unlocked'
      notifySuccess(`Adjusted ${line.material_code} — ${planMonth}, qty ${qty}${lockNote}.`)
      onSaved()
      onClose()
    } catch (err) {
      const msg = errMsg(err, 'Could not save this adjustment — please retry.')
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[90] flex justify-end bg-black/40">
      <div role="dialog" aria-modal="true" aria-label={`Adjust ${line.material_code}`} className="flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-neutral-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-neutral-900">Adjust Line</h2>
            <p className="font-mono text-xs text-neutral-500">{line.material_code} · demand {line.demand_month}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-neutral-600"
          >
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
          <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
            <FormField label="Quantity" required htmlFor="adjust-qty" error={errors.qty}>
              <Input
                id="adjust-qty"
                type="number"
                min="0"
                step="any"
                inputMode="decimal"
                value={qty}
                onChange={(e) => { setQty(e.target.value); setErrors((er) => ({ ...er, qty: undefined })) }}
                disabled={submitting}
                error={!!errors.qty}
              />
            </FormField>

            <FormField label="Plan Month" required htmlFor="adjust-plan-month" error={errors.plan_month} hint="Moving this line changes which month's capacity it occupies.">
              <Input
                id="adjust-plan-month"
                type="month"
                value={planMonth}
                onChange={(e) => { setPlanMonth(e.target.value); setErrors((er) => ({ ...er, plan_month: undefined })) }}
                disabled={submitting}
                error={!!errors.plan_month}
              />
            </FormField>

            <label className="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-neutral-700">
              <input
                type="checkbox"
                checked={locked}
                onChange={(e) => setLocked(e.target.checked)}
                disabled={submitting}
                className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500"
              />
              Locked (excluded from the next Recalculate)
            </label>

            {submitError && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2 border-t border-neutral-200 px-5 py-4">
            <Button type="button" variant="secondary" size="sm" className="min-h-[44px]" onClick={onClose} disabled={submitting}>Cancel</Button>
            <Button type="submit" size="sm" className="min-h-[44px]" disabled={submitting}>
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Save Changes
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
