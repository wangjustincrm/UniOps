// Week Exceptions — design §5.4's second new section: a per-week override
// of a standing capacity rule (`/capacity/exceptions`, app/api/v1/capacity.py
// — see that module's docstring for why exceptions get NO min<=max
// write-time check the standing rules do: the canonical example, a
// `max_output_qty=0` shutdown week under a standing `min_output_qty` rule,
// is exactly the case that check would wrongly reject). Scoped factory-wide
// only (`scope_type='factory', scope_ref=null`) — design §8 explicitly
// defers product_family/line capacity, so this form never offers a scope
// picker.
//
// List + add/deactivate only (brief: "列表 + 新增/停用") — no delete UI and
// no "reactivate" affordance of its own, even though the backend's DELETE
// and a plain is_active PATCH both exist: `MrpCapacityException`'s schema
// allows at most ONE row (active or not) per (week, factory scope,
// constraint_type) — see `findExistingException`'s doc in capacityApi.ts —
// so "Add" for a slot that already has a (possibly deactivated) row REUSES
// that row via PATCH rather than POSTing a second one, which would 409.
// That reuse is what stands in for "reactivate" here: re-adding the same
// week+constraint with `is_active` implicitly true on save IS the
// reactivation path, surfaced to the planner as a plain notice rather than
// a separate button.
//
// Week-start alignment is validated but NOT computed here: this file
// checks the date the planner typed LOOKS like a real week start under the
// current `week_calendar_mode` (Monday-start for the two ISO modes; day
// 1/8/15/22/29 for month_fixed) — a shallow, unambiguous check with no
// opinion on which MONTH a straddling week belongs to. That ownership
// question is exactly the subtle logic `week_calendar.py`'s docstring
// reserves for itself ("if mode-specific logic ever needs to leak outside
// week_calendar.py, that is a design smell") — this page has no MPS run in
// scope to source a real `week_grid` from (unlike AdjustDrawer/WeekDrawer,
// which always have one), so re-deriving month ownership here would be
// exactly that leak. Getting the ALIGNMENT wrong just means the exception
// silently matches no real week (`resolve_limits_for_week` matches
// `week_start` by exact equality) — the validation below exists to catch
// that before save, not to replace the backend's own week math.
import { useMemo, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Plus, X as XIcon } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import {
  capacityApi, WEEK_START_DOW_LABEL, type WeekStartDow, CONSTRAINT_TYPE_LABEL, findExistingException,
  type CapacityConstraintType, type CapacityException, type WeekCalendarMode,
} from './capacityApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

function isWeekCalendarMode(v: unknown): v is WeekCalendarMode {
  return v === 'iso_thursday' || v === 'iso_first_day' || v === 'month_fixed'
}

/** Alignment-only check — see this file's header comment for why this
 *  deliberately stops short of computing which month a straddling week
 *  belongs to. `dateStr` is a plain 'YYYY-MM-DD'; parsed as UTC midnight so
 *  the day-of-week/day-of-month read is never off by one across the
 *  caller's local timezone. */
function looksLikeWeekStart(
  dateStr: string, mode: WeekCalendarMode, startDow: WeekStartDow,
): boolean {
  const d = new Date(`${dateStr}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return false
  if (mode === 'month_fixed') {
    const day = d.getUTCDate()
    return day === 1 || day === 8 || day === 15 || day === 22 || day === 29
  }
  // Both ISO modes share the same boundary, and that boundary is whatever
  // `week_start_dow` says — hardcoding Monday here meant that once the
  // factory switched to Saturday-start weeks, NO date the planner could
  // type was accepted and maintenance weeks became unenterable.
  return d.getUTCDay() === jsWeekday(startDow)
}

/** Python's `date.weekday()` (0=Monday..6=Sunday, what `week_start_dow`
 *  stores) to JavaScript's `getUTCDay()` (0=Sunday..6=Saturday). */
function jsWeekday(startDow: WeekStartDow): number {
  return (startDow + 1) % 7
}

interface FormErrors {
  week_start?: string
  limit_value?: string
  reason?: string
}

interface FormState {
  week_start: string
  constraint_type: CapacityConstraintType
  limit_value: string
  reason: string
}

function initialForm(): FormState {
  return { week_start: '', constraint_type: 'max_output_qty', limit_value: '', reason: '' }
}

export function WeekExceptionsSection({
  canWrite, notifySuccess, notifyError,
}: {
  canWrite: boolean
  /** Round-1 review finding #4: Add/Deactivate must never fail silently —
   *  wired to the page's `useToasts()`, same `notifySuccess`/`notifyError`
   *  contract every drawer in this app already uses (AdjustDrawer,
   *  RuleDrawer, WeekDrawer). */
  notifySuccess: (message: string) => void
  notifyError: (message: string) => void
}) {
  const queryClient = useQueryClient()

  const exceptionsQuery = useQuery({
    queryKey: ['capacity-exceptions'],
    queryFn: () => capacityApi.listExceptions(),
  })
  const exceptions = useMemo(
    () => [...(exceptionsQuery.data ?? [])].sort((a, b) => b.week_start.localeCompare(a.week_start)),
    [exceptionsQuery.data],
  )

  // Same cache the Planning Calendar section reads — react-query dedupes
  // the request, this just needs the mode for the alignment check above.
  const paramsQuery = useQuery({ queryKey: ['mrp-params'], queryFn: () => capacityApi.getParams() })
  const rawDow = paramsQuery.data?.week_start_dow
  const startDow: WeekStartDow = (typeof rawDow === 'number' && Number.isInteger(rawDow)
    && rawDow >= 0 && rawDow <= 6) ? rawDow as WeekStartDow : 0
  const mode: WeekCalendarMode = isWeekCalendarMode(paramsQuery.data?.week_calendar_mode)
    ? paramsQuery.data!.week_calendar_mode as WeekCalendarMode
    : 'iso_thursday'

  const [formOpen, setFormOpen] = useState(false)
  const [form, setForm] = useState<FormState>(initialForm)
  const [errors, setErrors] = useState<FormErrors>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [deactivatingId, setDeactivatingId] = useState<string | null>(null)
  const [deactivateError, setDeactivateError] = useState<string | null>(null)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }
  function clearError(key: keyof FormErrors) {
    setErrors((e) => (e[key] ? { ...e, [key]: undefined } : e))
  }

  function validateWeekStart(v: string): string | undefined {
    if (!v) return 'Select a week start date.'
    if (!looksLikeWeekStart(v, mode, startDow)) {
      return mode === 'month_fixed'
        ? 'Must be the 1st, 8th, 15th, 22nd or 29th of a month under Month Fixed mode.'
        : `Must be a ${WEEK_START_DOW_LABEL[startDow]} — weeks currently start on ${WEEK_START_DOW_LABEL[startDow]}.`
    }
    return undefined
  }
  function validateLimitValue(v: string): string | undefined {
    const n = Number(v)
    if (v.trim() === '' || !Number.isFinite(n) || n < 0) return 'Enter a value of 0 or more.'
    return undefined
  }
  function validateReason(v: string): string | undefined {
    if (!v.trim()) return 'Enter a reason for this exception.'
    return undefined
  }

  const existingMatch = form.week_start && !validateWeekStart(form.week_start)
    ? findExistingException(exceptions, {
      week_start: form.week_start, scope_type: 'factory', scope_ref: null, constraint_type: form.constraint_type,
    })
    : null

  function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ['capacity-exceptions'] })
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const errs: FormErrors = {
      week_start: validateWeekStart(form.week_start),
      limit_value: validateLimitValue(form.limit_value),
      reason: validateReason(form.reason),
    }
    setErrors(errs)
    if (Object.values(errs).some(Boolean)) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      const uom = form.constraint_type === 'max_sku_count' ? null : 'KG'
      if (existingMatch) {
        await capacityApi.updateException(existingMatch.id, {
          limit_value: Number(form.limit_value), uom, reason: form.reason.trim(), is_active: true,
        })
      } else {
        await capacityApi.createException({
          week_start: form.week_start, scope_type: 'factory', scope_ref: null,
          constraint_type: form.constraint_type, limit_value: Number(form.limit_value), uom,
          reason: form.reason.trim(), is_active: true,
        })
      }
      notifySuccess(`Saved week exception — ${formatDate(form.week_start)} · ${CONSTRAINT_TYPE_LABEL[form.constraint_type]}.`)
      setFormOpen(false)
      setForm(initialForm())
      await invalidate()
    } catch (err) {
      const msg = errMsg(err, 'Could not save this exception — please retry.')
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  // Round-1 review finding #4: this used to `catch {}` with a comment
  // claiming "nothing to roll back" — true for the ROW STATE (it does stay
  // "Active"), but false for the PLANNER, who clicked Deactivate, saw
  // nothing happen, and had no way to tell a failed request from a slow
  // no-op. This codebase has already been bitten by exactly this class of
  // silent failure once (attachment downloads failing with no signal — see
  // project memory) — surfaced here the same way every other write path in
  // this file does: inline `role="alert"` plus a toast.
  async function handleDeactivate(row: CapacityException) {
    setDeactivatingId(row.id)
    setDeactivateError(null)
    try {
      await capacityApi.updateException(row.id, { is_active: false })
      notifySuccess(`Deactivated week exception — ${formatDate(row.week_start)} · ${CONSTRAINT_TYPE_LABEL[row.constraint_type]}.`)
      await invalidate()
    } catch (err) {
      const msg = errMsg(err, 'Could not deactivate this exception — please retry.')
      setDeactivateError(msg)
      notifyError(msg)
    } finally {
      setDeactivatingId(null)
    }
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-900">Week Exceptions</h2>
          <p className="mt-1 text-xs text-neutral-500">
            A one-week override of a standing rule — e.g. a <code>Max output / week</code> of 0 for an
            annual maintenance shutdown.
          </p>
        </div>
        {canWrite && (
          <Button type="button" size="sm" className="min-h-[44px]" onClick={() => { setForm(initialForm()); setErrors({}); setFormOpen(true) }}>
            <Plus className="h-3.5 w-3.5" /> Add exception
          </Button>
        )}
      </div>

      {deactivateError && (
        <p role="alert" className="mt-3 flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {deactivateError}
        </p>
      )}

      {exceptionsQuery.isError && (
        <p role="alert" className="mt-3 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {errMsg(exceptionsQuery.error, 'Could not load week exceptions.')}
        </p>
      )}

      {exceptionsQuery.isLoading ? (
        <p role="status" className="flex items-center justify-center gap-2 py-8 text-sm text-neutral-400">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading exceptions…
        </p>
      ) : exceptions.length === 0 ? (
        <p className="mt-3 text-sm text-neutral-500">No week exceptions yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto rounded-lg border border-neutral-200">
          <table className="min-w-full text-sm">
            <thead className="bg-neutral-50">
              <tr>
                <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Week</th>
                <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Constraint</th>
                <th className="border-b border-neutral-200 px-3 py-2 text-right text-[11px] font-semibold text-neutral-600">Value</th>
                <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Reason</th>
                <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Status</th>
                {canWrite && <th className="border-b border-neutral-200 px-3 py-2 text-right text-[11px] font-semibold text-neutral-600">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {exceptions.map((row) => (
                <tr key={row.id} className="border-b border-neutral-100 last:border-0 odd:bg-white even:bg-neutral-50/50">
                  <td className="px-3 py-2 text-xs text-neutral-800">{formatDate(row.week_start)}</td>
                  <td className="px-3 py-2 text-neutral-600">{CONSTRAINT_TYPE_LABEL[row.constraint_type]}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-neutral-800">
                    {formatQty(Number(row.limit_value))}{row.uom ? ` ${row.uom}` : ''}
                  </td>
                  <td className="max-w-xs truncate px-3 py-2 text-xs text-neutral-600" title={row.reason ?? undefined}>{row.reason ?? '—'}</td>
                  <td className="px-3 py-2">
                    <span
                      className={
                        row.is_active
                          ? 'inline-flex items-center rounded-full bg-success-50 px-2 py-0.5 text-xs font-medium text-success-700 ring-1 ring-inset ring-emerald-200'
                          : 'inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-500 ring-1 ring-inset ring-neutral-200'
                      }
                    >
                      {row.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  {canWrite && (
                    <td className="px-1 py-1 text-right">
                      {row.is_active && (
                        <button
                          type="button"
                          onClick={() => handleDeactivate(row)}
                          disabled={deactivatingId === row.id}
                          className="flex h-11 items-center gap-1 rounded-md px-3 text-xs font-medium text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800 disabled:opacity-60"
                        >
                          {deactivatingId === row.id && <Loader2 className="h-3 w-3 animate-spin" />}
                          Deactivate
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {formOpen && createPortal(
        <div className="fixed inset-0 z-[90] flex justify-end bg-black/40">
          <div role="dialog" aria-modal="true" aria-label="New week exception" className="flex h-full w-full max-w-md flex-col bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-neutral-200 px-5 py-4">
              <h2 className="text-base font-semibold text-neutral-900">New Week Exception</h2>
              <button
                type="button"
                onClick={() => setFormOpen(false)}
                aria-label="Close"
                className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-neutral-600"
              >
                <XIcon className="h-5 w-5" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
              <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
                <FormField
                  label="Week"
                  required
                  htmlFor="exception-week-start"
                  error={errors.week_start}
                  hint={mode === 'month_fixed' ? 'The 1st, 8th, 15th, 22nd or 29th of the month.' : 'The Monday that starts the week.'}
                >
                  <Input
                    id="exception-week-start"
                    type="date"
                    value={form.week_start}
                    onChange={(e) => { set('week_start', e.target.value); clearError('week_start') }}
                    onBlur={() => setErrors((er) => ({ ...er, week_start: validateWeekStart(form.week_start) }))}
                    disabled={submitting}
                    error={!!errors.week_start}
                  />
                </FormField>

                <FormField label="Constraint" required htmlFor="exception-constraint-type">
                  <select
                    id="exception-constraint-type"
                    value={form.constraint_type}
                    onChange={(e) => set('constraint_type', e.target.value as CapacityConstraintType)}
                    disabled={submitting}
                    className="flex h-10 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {(Object.keys(CONSTRAINT_TYPE_LABEL) as CapacityConstraintType[]).map((k) => (
                      <option key={k} value={k}>{CONSTRAINT_TYPE_LABEL[k]}</option>
                    ))}
                  </select>
                </FormField>

                <FormField label="Value" required htmlFor="exception-limit-value" error={errors.limit_value} hint="0 marks the week as fully closed (e.g. a maintenance shutdown).">
                  <Input
                    id="exception-limit-value"
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

                <FormField label="Reason" required htmlFor="exception-reason" error={errors.reason}>
                  <textarea
                    id="exception-reason"
                    rows={3}
                    value={form.reason}
                    onChange={(e) => { set('reason', e.target.value); clearError('reason') }}
                    onBlur={() => setErrors((er) => ({ ...er, reason: validateReason(form.reason) }))}
                    disabled={submitting}
                    className="flex w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-60"
                  />
                </FormField>

                {existingMatch && (
                  <p role="status" className="flex items-start gap-1.5 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>
                      An exception already exists for this week and constraint
                      {!existingMatch.is_active ? ' (currently inactive)' : ''} — saving will update it in place
                      rather than creating a second one.
                    </span>
                  </p>
                )}

                {submitError && (
                  <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                    <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
                  </p>
                )}
              </div>

              <div className="flex justify-end gap-2 border-t border-neutral-200 px-5 py-4">
                <Button type="button" variant="secondary" size="sm" className="min-h-[44px]" onClick={() => setFormOpen(false)} disabled={submitting}>Cancel</Button>
                <Button type="submit" size="sm" className="min-h-[44px]" disabled={submitting}>
                  {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Save
                </Button>
              </div>
            </form>
          </div>
        </div>,
        document.body,
      )}
    </section>
  )
}
