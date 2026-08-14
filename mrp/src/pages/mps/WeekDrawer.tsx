// WeekDrawer — mark/unmark a maintenance week from the Production Plan
// matrix's week header (design §5.1's last bullet, added 2026-08-13 — the
// newest requirement in the spec, added after everything else in §5 was
// already written). Same click-to-open interaction the matrix already uses
// for a Planned cell (AdjustDrawer) — clicking a week COLUMN HEADER opens
// this, no right-click anywhere. Copies AdjustDrawer's skeleton verbatim:
// `createPortal` to document.body, `fixed inset-0 z-[90] flex justify-end
// bg-black/40`, Cancel/Save, `min-h-[44px]` touch targets.
//
// A "maintenance week" is a `max_output_qty=0` capacity exception, scoped
// factory-wide (design §8: no product_family/line capacity this phase) —
// the SAME `/capacity/exceptions` row the standalone Week Exceptions
// section (capacity/WeekExceptionsSection.tsx) manages, just with a fixed
// constraint_type/value and a friendlier single-purpose form. Both write
// through the identical upsert rule (see capacityApi.ts's
// `findExistingException` doc): at most one exception row — active or not
// — can ever exist for a given (week, factory scope, constraint_type), a
// DB-level guarantee (`MrpCapacityException`'s docstring), so re-marking a
// week that was PREVIOUSLY marked and then unmarked must PATCH that same
// row back to `is_active=true`, never POST a second one (that would 409).
//
// **This drawer does not re-plan anything.** The exception only changes
// what the NEXT generate/recalculate sees — a released run is a frozen
// snapshot, and an already-open (draft) run's existing lines don't move
// just because a week's ceiling changed under them. Saving therefore never
// calls recalculate itself; it offers it, via a toast with a
// "Recalculate now" action button (see useToasts.ts's `ToastAction`) — the
// planner decides when to apply it, so a hand adjustment they just made
// elsewhere in the same run doesn't get silently swept away. `onRecalculate`
// is OPTIONAL for exactly the same reason the toolbar's own Recalculate
// button is disabled on a released run (mps.py's `recalculate_run` 409s):
// the page passes `undefined` for a released run, and the success toast
// simply omits the action rather than offering a button that would error.
//
// **No production apportionment happens here, or anywhere in this file.**
// Re-levelling within the month (120t/4 weeks becoming 40/40/40 once one
// week closes) is entirely the engine's job, exercised the next time it
// runs — this drawer only ever writes a single exception row.
//
// ## Round-1 review fixes (2026-08-14)
//
// 1. `initialMaintenance` used to be `!!existingException?.is_active` with
//    NO check on `limit_value` — so a legitimate non-zero
//    `max_output_qty` override (a planner de-rating a short week via Week
//    Exceptions, e.g. to 20000) showed up here as a CHECKED maintenance
//    box on an UNTINTED column (the matrix's tint correctly checks
//    value===0), and unchecking it would PATCH `is_active: false`,
//    silently destroying that override under a maintenance-worded message.
//    Fixed: `isMaintenanceRow` now requires BOTH `is_active` AND
//    `Number(limit_value) === 0`; a non-zero ACTIVE row is treated as a
//    capacity override this drawer refuses to touch (see
//    `nonZeroActiveOverride` below) rather than being silently offered as
//    "maintenance".
// 2. `existingException` is sourced from the page's `capacity-exceptions`
//    query, which can be loading or errored when this drawer opens (no
//    error surface existed on the page before). Fixed: the page now passes
//    `existingExceptionLoading`/`existingExceptionError`, and this drawer
//    withholds the checkbox/reason/Save behind a loading or error state
//    rather than rendering (possibly wrong) form state against stale data.
//    Belt-and-suspenders: `handleSubmit` ALSO re-reads
//    `/capacity/exceptions` fresh immediately before deciding POST vs.
//    PATCH, rather than trusting whatever `existingException` prop was
//    current when the drawer opened — closes the race where the query
//    resolves or changes between open and Save.
import { useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Info, Loader2, Wrench, X as XIcon } from 'lucide-react'
import { Button, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import type { ToastAction } from '@/hooks/useToasts'
import type { WeekGridEntry } from './mpsApi'
import { capacityApi, findExistingException, type CapacityException } from '../capacity/capacityApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

/** `week.label` is `'Aug W1 · Aug 3–9'` (short label + day range) when the
 *  server provides one — split it so the drawer can show both, same
 *  convention ProductionMatrix.tsx's `weekColumnShortLabel` follows for the
 *  header cell's short form. */
function splitWeekLabel(label: string | undefined, fallback: string): { short: string; range: string | null } {
  if (!label) return { short: fallback, range: null }
  const idx = label.indexOf(' · ')
  return idx === -1 ? { short: label, range: null } : { short: label.slice(0, idx), range: label.slice(idx + 3) }
}

/** True exactly for the row this drawer is allowed to own: an ACTIVE
 *  `max_output_qty` exception whose value is exactly 0 — a shutdown, not a
 *  de-rate. Matches ProductionMatrix's/ProductionPlanPage's own
 *  `maintenanceWeekStarts` definition field-for-field (round-1 finding #1's
 *  fix) so the tint and this drawer's checkbox can never disagree. */
function isMaintenanceRow(e: CapacityException | null): boolean {
  return !!e && e.is_active && Number(e.limit_value) === 0
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

export function WeekDrawer({
  week, existingException, existingExceptionLoading, existingExceptionError,
  canWrite, onClose, onSaved, onRecalculate, notifySuccess, notifyError,
}: {
  week: WeekGridEntry
  /** The current `/capacity/exceptions` row for this exact week + factory
   *  scope + `constraint_type='max_output_qty'`, active or not (see this
   *  file's header comment on why "or not" matters for the upsert rule) —
   *  or `null` if none exists yet. Sourced by the page from its own
   *  `capacity-exceptions` query so this drawer never fetches on its own
   *  for the INITIAL render — `handleSubmit` re-fetches fresh before
   *  deciding create vs. update regardless, see round-1 fix #2. */
  existingException: CapacityException | null
  /** True while the page's exceptions query hasn't resolved yet — the
   *  checkbox/reason/Save stay withheld rather than rendering against data
   *  that might be stale or simply not there yet (round-1 fix #2). */
  existingExceptionLoading: boolean
  /** Non-null when the page's exceptions query errored — same withholding
   *  as the loading case, plus the message is shown so the planner
   *  understands why the form isn't interactive rather than reading it as
   *  broken. */
  existingExceptionError: string | null
  /** `mrp.param.write` — gates the checkbox and Save, not the drawer
   *  itself: a read-only caller (`mrp.report.view`) can still open this
   *  and see the current state and reason (design §5.1: "无权限时勾选框
   *  禁用并写明原因，读权限仍可打开抽屉看状态"). */
  canWrite: boolean
  onClose: () => void
  /** Called after a successful save so the page can refetch both the run
   *  (its week columns' tint reflects the new exception) and the
   *  exceptions list. Does not close the drawer — same split every other
   *  drawer here uses. */
  onSaved: () => void
  /** Bound to the page's own Recalculate handler — wired into the success
   *  toast's action button rather than called directly (see header
   *  comment: marking a week never recalculates by itself).
   *  `undefined` on a released run (recalculate 409s there) — the toast
   *  then omits the action button entirely instead of offering one that
   *  would error (round-1 fix #5a). */
  onRecalculate?: () => void
  notifySuccess: (message: string, action?: ToastAction) => void
  notifyError: (message: string) => void
}) {
  // A non-zero ACTIVE exception at this slot is a legitimate capacity
  // override (e.g. a planner de-rated this week to 20000 via Week
  // Exceptions) — round-1 fix #1: this drawer must never represent that as
  // "maintenance" (it isn't 0) nor let unchecking a box silently deactivate
  // it. Detected from whatever `existingException` the page currently has;
  // re-checked against the fresh read in `handleSubmit` too.
  const nonZeroActiveOverride = !!existingException?.is_active && Number(existingException.limit_value) !== 0

  const initialMaintenance = isMaintenanceRow(existingException)
  const [maintenance, setMaintenance] = useState(initialMaintenance)
  const [reason, setReason] = useState(existingException?.reason ?? '')
  const [reasonError, setReasonError] = useState<string | undefined>()
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const dataUnready = existingExceptionLoading || !!existingExceptionError
  // The whole form (not just Save) is disabled while data is unready or the
  // slot is a non-zero override this drawer refuses to touch — both are
  // "don't let the planner interact with state we can't vouch for" cases.
  const formDisabled = submitting || dataUnready || nonZeroActiveOverride || !canWrite

  const { short: weekShort, range: weekRange } = splitWeekLabel(week.label, week.week_start)

  function validateReason(nextMaintenance: boolean, value: string): string | undefined {
    if (nextMaintenance && !value.trim()) return 'Enter a reason for this maintenance week.'
    return undefined
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canWrite || nonZeroActiveOverride) return
    const err = validateReason(maintenance, reason)
    setReasonError(err)
    if (err) return

    const trimmedReason = reason.trim()
    setSubmitting(true)
    setSubmitError(null)
    try {
      // Round-1 fix #2: re-read fresh rather than trusting the
      // possibly-stale `existingException` prop for the create-vs-update
      // decision — closes the race where the page's query hadn't resolved
      // (or resolved to something else) between this drawer opening and
      // Save being clicked. This is the SAME upsert rule
      // (`findExistingException`) every other write path in this app uses.
      const fresh = await capacityApi.listExceptions()
      const current = findExistingException(fresh, {
        week_start: week.week_start, scope_type: 'factory', scope_ref: null, constraint_type: 'max_output_qty',
      })
      if (current?.is_active && Number(current.limit_value) !== 0) {
        // Someone else turned this into a non-zero override between open
        // and Save — refuse rather than clobber it, same as the
        // render-time guard above.
        throw new ApiError(
          'This week now has a non-zero Max output / week override — reopen this drawer to see the current state.',
          409, null,
        )
      }
      const changed = maintenance !== isMaintenanceRow(current) ||
        (maintenance && trimmedReason !== (current?.reason ?? ''))
      if (!changed) { onClose(); return }

      if (maintenance) {
        if (current) {
          // Reactivate/update the one row this (week, scope, constraint)
          // slot is allowed to have — never a second POST, see header
          // comment.
          await capacityApi.updateException(current.id, {
            limit_value: 0, uom: 'KG', reason: trimmedReason, is_active: true,
          })
        } else {
          await capacityApi.createException({
            week_start: week.week_start, scope_type: 'factory', scope_ref: null,
            constraint_type: 'max_output_qty', limit_value: 0, uom: 'KG',
            reason: trimmedReason, is_active: true,
          })
        }
      } else if (current) {
        await capacityApi.updateException(current.id, {
          is_active: false, reason: trimmedReason || null,
        })
      }
      notifySuccess(
        `${weekShort} ${maintenance ? 'marked as a maintenance week' : 'no longer marked as a maintenance week'} — ` +
        'this only affects the next generate or recalculate, not runs already open.',
        // Round-1 fix #5a: only offer the action when the caller gave us
        // one — the page omits it for a released run, where recalculate
        // would just 409.
        onRecalculate ? { label: 'Recalculate now', onClick: onRecalculate } : undefined,
      )
      onSaved()
      onClose()
    } catch (err2) {
      const msg = errMsg(err2, 'Could not save this week exception — please retry.')
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[90] flex justify-end bg-black/40">
      <div role="dialog" aria-modal="true" aria-label={`Week ${weekShort}`} className="flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-neutral-200 px-5 py-4">
          <div>
            <h2 className="flex items-center gap-1.5 text-base font-semibold text-neutral-900">
              {maintenance && <Wrench aria-hidden className="h-4 w-4 text-neutral-500" />}
              {weekShort}
            </h2>
            {weekRange && <p className="text-xs text-neutral-500">{weekRange}</p>}
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
            {existingExceptionLoading && (
              <p role="status" className="flex items-center gap-2 text-sm text-neutral-500">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading this week's current state…
              </p>
            )}

            {existingExceptionError && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {existingExceptionError}
              </p>
            )}

            {nonZeroActiveOverride && existingException && (
              <p role="status" className="flex items-start gap-1.5 rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-xs text-primary-800">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  This week already has a Max output / week override of {formatQty(Number(existingException.limit_value))}
                  {existingException.uom ? ` ${existingException.uom}` : ''} — a capacity reduction, not a shutdown. This
                  drawer only manages maintenance (0-output) weeks; edit or remove that override from Capacity Rules →
                  Week Exceptions instead.
                </span>
              </p>
            )}

            <label className="flex min-h-[44px] cursor-pointer items-start gap-2 text-sm text-neutral-700">
              <input
                type="checkbox"
                checked={maintenance}
                onChange={(e) => {
                  setMaintenance(e.target.checked)
                  setReasonError(validateReason(e.target.checked, reason))
                }}
                disabled={formDisabled}
                className="mt-0.5 h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500 disabled:cursor-not-allowed"
              />
              <span>
                Maintenance week (no production)
                <span className="block text-xs text-neutral-400">
                  Writes a Max output / week exception of 0 for this week — the engine schedules zero
                  production here and re-levels the rest of the month around it.
                </span>
              </span>
            </label>

            <FormField
              label="Reason"
              required={maintenance}
              htmlFor="week-drawer-reason"
              error={reasonError}
              hint={
                nonZeroActiveOverride
                  ? undefined
                  : !canWrite
                    ? 'Read-only — you do not have permission to change this.'
                    : undefined
              }
            >
              <textarea
                id="week-drawer-reason"
                rows={3}
                value={reason}
                onChange={(e) => {
                  setReason(e.target.value)
                  setReasonError((prev) => (prev ? validateReason(maintenance, e.target.value) : prev))
                }}
                onBlur={() => setReasonError(validateReason(maintenance, reason))}
                disabled={formDisabled}
                placeholder="e.g. annual maintenance shutdown"
                className="flex w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:bg-neutral-50 disabled:opacity-80"
              />
            </FormField>

            {submitError && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2 border-t border-neutral-200 px-5 py-4">
            <Button type="button" variant="secondary" size="sm" className="min-h-[44px]" onClick={onClose} disabled={submitting}>
              {canWrite && !nonZeroActiveOverride ? 'Cancel' : 'Close'}
            </Button>
            {canWrite && !nonZeroActiveOverride && (
              <Button type="submit" size="sm" className="min-h-[44px]" disabled={formDisabled}>
                {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Save Changes
              </Button>
            )}
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
