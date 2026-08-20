// Create/Edit form for a single capacity rule — design §6.6 page 2, Phase
// 1B Task 5. Portaled to document.body as a right-side drawer (repo
// convention for any floating panel — see MaterialPicker.tsx's header note
// and feedback_uniops_overlay_dropdown_portal: a plain `absolute` panel
// gets clipped by an ancestor's overflow; a fixed-position drawer sidesteps
// that entirely).
//
// Progressive fields (design spec): `scope_ref` only makes sense for
// product_family/line scopes (factory-wide rules have no ref); `uom` only
// makes sense for max_output_qty (a SKU-count limit has no unit). Both are
// hidden — not just disabled — for the inapplicable constraint/scope so the
// form never implies a value is expected where the backend wouldn't accept
// one meaningfully, and cleared in state when hidden so a stale value from
// a prior selection can't leak into the submitted body.
//
// Validation runs onBlur per field (errors shown beside the field via
// FormField's `error` prop, which renders role="alert" — see
// @uniops/shell/ui/form-field.tsx) and again in full on submit. The
// overlap check is a separate, non-blocking signal: an inline warning, not
// a field error, computed client-side against the rules list the page
// already has loaded (no dedicated backend endpoint for this — the
// resolve_effective_rules() overlap the brief describes is a planning-time
// concern for the MPS engine, not a create-time hard constraint here).
import { useMemo, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Loader2, X as XIcon } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { MaterialPicker } from '@/pages/consignment/MaterialPicker'
import {
  capacityApi,
  SCOPE_TYPE_LABEL,
  CONSTRAINT_TYPE_LABEL,
  type CapacityRule,
  type CapacityRuleBody,
  type CapacityScopeType,
  type CapacityConstraintType,
} from './capacityApi'

// Output-quantity capacity is KG-only, and not just as a UI default: the
// MPS engine that consumes these rules (mrp-api's app/services/capacity.py
// resolve_limits_for_week) reads `limit_value` as a bare Decimal and
// enforces it as kilograms — it never looks at the `uom` column to convert.
// A rule saved with any other unit (e.g. "50" meant as tonnes) would
// silently be enforced as 50 kg, a 1000x error with no warning anywhere
// downstream. So this field must not be a free choice: it is fixed to
// 'KG', not offered as a picker, making a non-KG capacity rule impossible
// to save from this form. Applies to BOTH output-quantity constraint types
// (max_output_qty and min_output_qty) — max_sku_count is a bare count and
// carries no unit at all.
const OUTPUT_UOM = 'KG'

function isOutputQtyConstraint(t: CapacityConstraintType): boolean {
  return t === 'max_output_qty' || t === 'min_output_qty'
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

interface FormErrors {
  scope_ref?: string
  limit_value?: string
  effective_from?: string
  effective_to?: string
}

interface FormState {
  scope_type: CapacityScopeType
  scope_ref: string
  constraint_type: CapacityConstraintType
  limit_value: string
  effective_from: string
  effective_to: string
  is_active: boolean
}

// `uom` is deliberately not part of FormState — it is never a user choice
// (see OUTPUT_UOM above). It's derived at submit time and for display,
// always 'KG' for max_output_qty and null otherwise, regardless of what a
// loaded rule's `uom` column happens to contain.
function initialState(rule: CapacityRule | null): FormState {
  if (!rule) {
    return {
      scope_type: 'factory',
      scope_ref: '',
      constraint_type: 'max_sku_count',
      limit_value: '',
      effective_from: todayIso(),
      effective_to: '',
      is_active: true,
    }
  }
  return {
    scope_type: rule.scope_type,
    scope_ref: rule.scope_ref ?? '',
    constraint_type: rule.constraint_type,
    limit_value: rule.limit_value,
    effective_from: rule.effective_from,
    effective_to: rule.effective_to ?? '',
    is_active: rule.is_active,
  }
}

/** Whole-window overlap: [aFrom, aTo] vs [bFrom, bTo], either end open
 *  (null = unbounded). ISO 'YYYY-MM-DD' strings sort correctly with plain
 *  `<=`, so no Date parsing is needed. */
function windowsOverlap(aFrom: string, aTo: string | null, bFrom: string, bTo: string | null): boolean {
  const aEnd = aTo ?? '9999-12-31'
  const bEnd = bTo ?? '9999-12-31'
  return aFrom <= bEnd && bFrom <= aEnd
}

function findOverlappingRule(
  rules: CapacityRule[],
  candidate: {
    scope_type: CapacityScopeType
    scope_ref: string | null
    constraint_type: CapacityConstraintType
    effective_from: string
    effective_to: string | null
  },
  excludeId: string | undefined,
): CapacityRule | null {
  if (!candidate.effective_from) return null
  return (
    rules.find(
      (r) =>
        r.id !== excludeId &&
        r.is_active &&
        r.scope_type === candidate.scope_type &&
        (r.scope_ref ?? null) === (candidate.scope_ref ?? null) &&
        r.constraint_type === candidate.constraint_type &&
        windowsOverlap(candidate.effective_from, candidate.effective_to, r.effective_from, r.effective_to),
    ) ?? null
  )
}

export function RuleDrawer({
  rule, existingRules, onClose, onSaved, notifySuccess, notifyError,
}: {
  /** null = create mode; a rule = edit mode (PATCH). */
  rule: CapacityRule | null
  /** Full current rules list (from the page's already-loaded query), used
   *  for the client-side overlap warning. */
  existingRules: CapacityRule[]
  onClose: () => void
  /** Called after a successful create/update so the page can invalidate
   *  its list query. Does not close the drawer itself — the caller does
   *  that via onClose, same split ForecastPage uses for its own modals. */
  onSaved: () => void
  notifySuccess: (message: string) => void
  notifyError: (message: string) => void
}) {
  const isEdit = !!rule
  const [form, setForm] = useState<FormState>(() => initialState(rule))
  const [errors, setErrors] = useState<FormErrors>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }
  function clearError(key: keyof FormErrors) {
    setErrors((e) => (e[key] ? { ...e, [key]: undefined } : e))
  }

  function handleScopeTypeChange(next: CapacityScopeType) {
    set('scope_type', next)
    if (next === 'factory') {
      // Factory-wide rules carry no ref — drop any value typed while a
      // different scope was selected so it can't leak into the payload.
      set('scope_ref', '')
      clearError('scope_ref')
    }
  }

  function handleConstraintTypeChange(next: CapacityConstraintType) {
    set('constraint_type', next)
  }

  function validateScopeRef(scopeType: CapacityScopeType, scopeRef: string): string | undefined {
    if (scopeType === 'product' && !scopeRef.trim()) return 'Select a product.'
    if (scopeType !== 'factory' && !scopeRef.trim()) return 'Enter a scope reference.'
    return undefined
  }
  function validateLimitValue(v: string): string | undefined {
    const n = Number(v)
    if (v.trim() === '' || !Number.isFinite(n) || n <= 0) return 'Enter a limit greater than 0.'
    return undefined
  }
  function validateEffectiveFrom(v: string): string | undefined {
    if (!v) return 'Select a start date.'
    return undefined
  }
  function validateEffectiveTo(from: string, to: string): string | undefined {
    if (to && from && to < from) return 'End date must be on or after the start date.'
    return undefined
  }

  function validateAll(): FormErrors {
    return {
      scope_ref: validateScopeRef(form.scope_type, form.scope_ref),
      limit_value: validateLimitValue(form.limit_value),
      effective_from: validateEffectiveFrom(form.effective_from),
      effective_to: validateEffectiveTo(form.effective_from, form.effective_to),
    }
  }

  const overlap = useMemo(
    () =>
      findOverlappingRule(
        existingRules,
        {
          scope_type: form.scope_type,
          scope_ref: form.scope_type === 'factory' ? null : form.scope_ref.trim() || null,
          constraint_type: form.constraint_type,
          effective_from: form.effective_from,
          effective_to: form.effective_to || null,
        },
        rule?.id,
      ),
    [existingRules, form.scope_type, form.scope_ref, form.constraint_type, form.effective_from, form.effective_to, rule?.id],
  )

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const errs = validateAll()
    setErrors(errs)
    if (Object.values(errs).some(Boolean)) return

    const body: CapacityRuleBody = {
      scope_type: form.scope_type,
      scope_ref: form.scope_type === 'factory' ? null : form.scope_ref.trim(),
      constraint_type: form.constraint_type,
      limit_value: Number(form.limit_value),
      // Fixed, never user-editable — see OUTPUT_UOM's header comment: the
      // MPS engine enforces this number as KG unconditionally, so any
      // other recorded unit would silently misrepresent the limit.
      uom: isOutputQtyConstraint(form.constraint_type) ? OUTPUT_UOM : null,
      effective_from: form.effective_from,
      effective_to: form.effective_to || null,
      is_active: form.is_active,
    }

    setSubmitting(true)
    setSubmitError(null)
    try {
      const saved = isEdit ? await capacityApi.update(rule!.id, body) : await capacityApi.create(body)
      const scopeLabel = saved.scope_ref ? `${SCOPE_TYPE_LABEL[saved.scope_type]} · ${saved.scope_ref}` : SCOPE_TYPE_LABEL[saved.scope_type]
      notifySuccess(`${isEdit ? 'Updated' : 'Created'} rule — ${scopeLabel} · ${CONSTRAINT_TYPE_LABEL[saved.constraint_type]}.`)
      onSaved()
      onClose()
    } catch (err) {
      const msg = errMsg(err, `Could not ${isEdit ? 'update' : 'create'} this rule — please retry.`)
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[90] flex justify-end bg-black/40">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? 'Edit capacity rule' : 'New capacity rule'}
        className="flex h-full w-full max-w-md flex-col bg-white shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-neutral-200 px-5 py-4">
          <h2 className="text-base font-semibold text-neutral-900">{isEdit ? 'Edit Capacity Rule' : 'New Capacity Rule'}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-neutral-400 hover:text-neutral-600">
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
          <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
            <FormField label="Scope" required htmlFor="rule-scope-type">
              <select
                id="rule-scope-type"
                value={form.scope_type}
                onChange={(e) => handleScopeTypeChange(e.target.value as CapacityScopeType)}
                disabled={submitting}
                className="flex h-10 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {(Object.keys(SCOPE_TYPE_LABEL) as CapacityScopeType[]).map((k) => (
                  <option key={k} value={k}>{SCOPE_TYPE_LABEL[k]}</option>
                ))}
              </select>
            </FormField>

            {form.scope_type === 'product' && (
              <FormField
                label="Product"
                required
                htmlFor="rule-scope-ref"
                error={errors.scope_ref}
                hint="The minimum lot size applies to this product only."
              >
                <MaterialPicker
                  value={form.scope_ref}
                  onSelect={(m) => { set('scope_ref', m.code); clearError('scope_ref') }}
                  onClear={() => set('scope_ref', '')}
                  hasError={!!errors.scope_ref}
                  disabled={submitting}
                />
              </FormField>
            )}

            {form.scope_type !== 'factory' && form.scope_type !== 'product' && (
              <FormField
                label={form.scope_type === 'product_family' ? 'Product Family' : 'Line'}
                required
                htmlFor="rule-scope-ref"
                error={errors.scope_ref}
                hint={form.scope_type === 'product_family' ? 'e.g. a product family code.' : 'e.g. a production line code.'}
              >
                <Input
                  id="rule-scope-ref"
                  value={form.scope_ref}
                  onChange={(e) => { set('scope_ref', e.target.value); clearError('scope_ref') }}
                  onBlur={() => setErrors((er) => ({ ...er, scope_ref: validateScopeRef(form.scope_type, form.scope_ref) }))}
                  disabled={submitting}
                  error={!!errors.scope_ref}
                />
              </FormField>
            )}

            <FormField label="Constraint" required htmlFor="rule-constraint-type">
              <select
                id="rule-constraint-type"
                value={form.constraint_type}
                onChange={(e) => handleConstraintTypeChange(e.target.value as CapacityConstraintType)}
                disabled={submitting}
                className="flex h-10 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {(Object.keys(CONSTRAINT_TYPE_LABEL) as CapacityConstraintType[]).map((k) => (
                  <option key={k} value={k}>{CONSTRAINT_TYPE_LABEL[k]}</option>
                ))}
              </select>
            </FormField>

            <div className="grid grid-cols-2 gap-3">
              <FormField label="Limit" required htmlFor="rule-limit-value" error={errors.limit_value}>
                <Input
                  id="rule-limit-value"
                  type="number"
                  min="0"
                  step="any"
                  inputMode="decimal"
                  value={form.limit_value}
                  onChange={(e) => { set('limit_value', e.target.value); clearError('limit_value') }}
                  onBlur={() => setErrors((er) => ({ ...er, limit_value: validateLimitValue(form.limit_value) }))}
                  placeholder="0"
                  disabled={submitting}
                  error={!!errors.limit_value}
                />
              </FormField>

              {isOutputQtyConstraint(form.constraint_type) && (
                <FormField
                  label="Unit"
                  hint="Fixed — the planning engine enforces this limit in kilograms only."
                >
                  {/* Read-only, not a picker: see OUTPUT_UOM's header comment
                      — the MPS engine has no unit conversion, so this field
                      must never be user-editable. A disabled Input (rather
                      than plain text) keeps the same field height/alignment
                      as the Limit input beside it. */}
                  <Input id="rule-uom" value={OUTPUT_UOM} readOnly disabled />
                </FormField>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <FormField label="Effective From" required htmlFor="rule-effective-from" error={errors.effective_from}>
                <Input
                  id="rule-effective-from"
                  type="date"
                  value={form.effective_from}
                  onChange={(e) => { set('effective_from', e.target.value); clearError('effective_from') }}
                  onBlur={() => setErrors((er) => ({
                    ...er,
                    effective_from: validateEffectiveFrom(form.effective_from),
                    effective_to: validateEffectiveTo(form.effective_from, form.effective_to),
                  }))}
                  disabled={submitting}
                  error={!!errors.effective_from}
                />
              </FormField>

              <FormField label="Effective To" htmlFor="rule-effective-to" error={errors.effective_to} hint="Leave blank for open-ended.">
                <Input
                  id="rule-effective-to"
                  type="date"
                  value={form.effective_to}
                  onChange={(e) => { set('effective_to', e.target.value); clearError('effective_to') }}
                  onBlur={() => setErrors((er) => ({ ...er, effective_to: validateEffectiveTo(form.effective_from, form.effective_to) }))}
                  disabled={submitting}
                  error={!!errors.effective_to}
                />
              </FormField>
            </div>

            <label className="flex items-center gap-2 text-sm text-neutral-700">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => set('is_active', e.target.checked)}
                disabled={submitting}
                className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500"
              />
              Active
            </label>

            {/* Overlap warning — inline, non-blocking (design spec: warn, don't
                block). role="status" not "alert": this doesn't prevent saving,
                it's advisory information about what will co-exist. */}
            {overlap && (
              <div role="status" className="flex items-start gap-1.5 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  Overlaps an existing active rule for the same scope and constraint (
                  {formatQty(Number(overlap.limit_value))}{overlap.uom ? ` ${overlap.uom}` : ''}, {overlap.effective_from}
                  {' – '}{overlap.effective_to ?? 'open-ended'}). Both will be considered active over the overlapping period.
                </span>
              </div>
            )}

            {submitError && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2 border-t border-neutral-200 px-5 py-4">
            <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={submitting}>Cancel</Button>
            <Button type="submit" size="sm" disabled={submitting}>
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {isEdit ? 'Save Changes' : 'Create Rule'}
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
