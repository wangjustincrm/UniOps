// Adjust a single MPS line's qty/week/lock — design §5.2. Portaled to
// document.body as a right-side drawer, same convention as
// capacity/RuleDrawer.tsx (see that file's header note and
// feedback_uniops_overlay_dropdown_portal in project memory).
//
// Weekly rework (Task 10): "change plan month" became "change plan WEEK".
// The week picker offers exactly the run's own `week_grid` (never computed
// client-side — week boundary/ownership math is `week_calendar.py`'s alone,
// see mpsApi.ts's WeekGridEntry doc), and crossing into a different month is
// allowed (the picker is not filtered to the line's current month). The
// server re-derives `plan_week_month`/`weeks_early` and re-runs the engine's
// own shelf-life rule on every move — a move it refuses comes back as a 422
// (mps.py's `update_line` docstring: "REJECTED, not stored with
// shelf_life_ok=False"), surfaced here verbatim rather than swallowed.
//
// Locking used to be a bulk action from MpsLineTable's own action bar (row
// checkboxes + Lock/Unlock selected) — Production Plan Matrix Task 5 retired
// that table in favor of ProductionMatrix.tsx's per-cell click-to-adjust, so
// locking moved into this single-line form as a `locked_by_planner`
// checkbox instead of being dropped.
//
// **Merge into adjacent week** (design §5.2, D6's "two 20t weeks -> one
// 40t week"): moves this line onto the neighbouring week AND locks it. A
// PATCH here is single-line — it cannot combine this line's qty with a
// sibling line already sitting in the target week in one call. What
// actually folds two same-slot lines into one row is the engine's own
// `_merge_same_slot` (app/services/mps_engine.py), which only runs at
// generate/recalculate time, and only merges lines whose `locked` flag
// (among other fields) is IDENTICAL — see that function's key tuple. So
// this handler locks BOTH this line and any sibling line already at the
// target week (same material_code + demand_month, a real, non-gap line)
// before moving, and the visible "one 40t line" result appears once the
// planner next hits Recalculate (design's own manual-acceptance item 4:
// "调整抽屉把两周 20t 合成一周 40t，重算后不被冲掉" — the merge is set up
// here, folded on the next recalculate, and locking is what survives that
// recalculate). This mirrors mps.py's `recalculate_run` comment at the
// `locked_manual_adjusted` OR-not-overwrite line almost verbatim: "two
// locked rows can share a slot only if a planner moved one onto another".
import { useMemo, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, Loader2, X as XIcon } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { mpsApi, type MpsLine, type WeekGridEntry } from './mpsApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

interface FormErrors {
  qty?: string
  week?: string
}

function validate(qty: string, weekStart: string): FormErrors {
  const errs: FormErrors = {}
  const n = Number(qty)
  if (qty.trim() === '' || !Number.isFinite(n) || n < 0) errs.qty = 'Enter a quantity of 0 or more.'
  if (!weekStart) errs.week = 'Select a week.'
  return errs
}

function weekOptionLabel(w: WeekGridEntry): string {
  return w.label || w.week_start
}

export function AdjustDrawer({
  runId, line, weekGrid, allLines, onClose, onSaved, notifySuccess, notifyError,
}: {
  runId: string
  line: MpsLine
  /** The run's own week grid (`GET /runs/{id}`'s `week_grid`) — the ONLY
   *  source of "which weeks exist" for the picker and for merge's
   *  previous/next lookup. Never derive weeks from `allLines` (a
   *  maintenance week or a zero-net-demand month has no lines by
   *  construction) — see weekColumns.ts's header comment for the same
   *  rule applied to the matrix. */
  weekGrid: WeekGridEntry[]
  /** Every line of the current run, for merge's sibling lookup (is there
   *  already a line for this material_code + demand_month sitting in the
   *  adjacent week?). Passed whole rather than pre-filtered so this file
   *  owns the matching rule, in one place, next to the comment explaining
   *  why it matches `_merge_same_slot`'s key. */
  allLines: MpsLine[]
  onClose: () => void
  /** Called after a successful save so the page can refetch the run
   *  (qty/week changes shift capacity occupancy too) — does not close the
   *  drawer itself, same split RuleDrawer.tsx uses. */
  onSaved: () => void
  notifySuccess: (message: string) => void
  notifyError: (message: string) => void
}) {
  const [qty, setQty] = useState(line.qty)
  const [weekStart, setWeekStart] = useState(line.plan_week_start)
  const [locked, setLocked] = useState(line.locked_by_planner)
  const [errors, setErrors] = useState<FormErrors>({})
  const [submitting, setSubmitting] = useState(false)
  const [mergingDirection, setMergingDirection] = useState<'prev' | 'next' | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  // Set only after a save that MOVED the line to a different week — the
  // shelf-life check is only ever exercised (and worth showing) on a week
  // change; a qty-only or lock-only save closes immediately like before.
  // A 422 never reaches this state (it's surfaced as submitError instead,
  // and the drawer stays open on the unchanged form) — so whenever this is
  // set, `shelf_life_ok` is always true (see update_line's docstring: a
  // refused move is rejected, never stored with shelf_life_ok=False).
  const [saveResult, setSaveResult] = useState<MpsLine | null>(null)

  const sortedWeeks = useMemo(
    () => [...weekGrid].sort((a, b) => a.week_start.localeCompare(b.week_start)),
    [weekGrid],
  )
  const weekIndex = sortedWeeks.findIndex((w) => w.week_start === line.plan_week_start)
  const prevWeek = weekIndex > 0 ? sortedWeeks[weekIndex - 1] : null
  const nextWeek = weekIndex >= 0 && weekIndex < sortedWeeks.length - 1 ? sortedWeeks[weekIndex + 1] : null
  // A capacity_gap line is unmet demand, not committed production — merging
  // it would either try to lock a gap line (422, see update_line's own
  // refusal) or silently fold a shortfall into a real production quantity.
  // Disabled here rather than letting the request fail, same "don't offer
  // an action that only 422s" discipline the lock checkbox already follows
  // elsewhere in this file.
  const canMerge = !line.capacity_gap

  async function saveLine(body: Parameters<typeof mpsApi.adjustLine>[2], opts: { isWeekChange: boolean }) {
    const updated = await mpsApi.adjustLine(runId, line.id, body)
    if (opts.isWeekChange) {
      setSaveResult(updated)
      notifySuccess(`Moved ${updated.material_code} to the week of ${updated.week_label} — shelf life OK.`)
      onSaved()
    } else {
      const lockNote = body.locked_by_planner === undefined || body.locked_by_planner === line.locked_by_planner
        ? '' : body.locked_by_planner ? ', locked' : ', unlocked'
      notifySuccess(`Adjusted ${updated.material_code} — qty ${updated.qty}${lockNote}.`)
      onSaved()
      onClose()
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const errs = validate(qty, weekStart)
    setErrors(errs)
    if (Object.values(errs).some(Boolean)) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      await saveLine(
        { qty: Number(qty), plan_week_start: weekStart, locked_by_planner: locked },
        { isWeekChange: weekStart !== line.plan_week_start },
      )
    } catch (err) {
      const msg = errMsg(err, 'Could not save this adjustment — please retry.')
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleMerge(direction: 'prev' | 'next') {
    const target = direction === 'prev' ? prevWeek : nextWeek
    if (!target) return
    setMergingDirection(direction)
    setSubmitError(null)
    try {
      // A sibling line for the same (material_code, demand_month) already
      // sitting in the target week must ALSO be locked, or
      // `_merge_same_slot`'s per-field key (which includes `locked`) will
      // never treat the two as the same slot on the next recalculate — see
      // this file's header comment.
      const sibling = allLines.find((l) => (
        l.id !== line.id && !l.capacity_gap &&
        l.material_code === line.material_code &&
        l.demand_month === line.demand_month &&
        l.plan_week_start === target.week_start
      ))
      if (sibling && !sibling.locked_by_planner) {
        await mpsApi.adjustLine(runId, sibling.id, { locked_by_planner: true })
      }
      await saveLine({ plan_week_start: target.week_start, locked_by_planner: true }, { isWeekChange: true })
    } catch (err) {
      const msg = errMsg(err, 'Could not merge into that week — please retry.')
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setMergingDirection(null)
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

        {saveResult ? (
          // Post-save shelf-life result panel (design §5.2: "保存后显示保质期
          // 校验结果") — replaces the form once a week MOVE has succeeded,
          // rather than closing immediately, so the planner sees what the
          // engine decided about the new placement before the drawer goes
          // away.
          <div className="flex flex-1 flex-col overflow-hidden">
            <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
              <div role="status" className="flex items-start gap-1.5 rounded-md border border-success-200 bg-success-50 px-3 py-2 text-sm text-success-800">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  Shelf life check passed — now planned for <strong>{saveResult.week_label}</strong>
                  {saveResult.weeks_early > 0 ? ` (${saveResult.weeks_early} week${saveResult.weeks_early === 1 ? '' : 's'} early)` : ''}.
                </span>
              </div>
              {saveResult.is_prebuild && saveResult.prebuild_reason && (
                <p className="rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
                  {saveResult.prebuild_reason}
                </p>
              )}
            </div>
            <div className="flex justify-end gap-2 border-t border-neutral-200 px-5 py-4">
              <Button type="button" size="sm" className="min-h-[44px]" onClick={onClose}>Done</Button>
            </div>
          </div>
        ) : (
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

              <FormField
                label="Plan Week"
                required
                htmlFor="adjust-plan-week"
                error={errors.week}
                hint="Moving this line changes which week's capacity it occupies and re-runs the shelf-life check — crossing into a different month is allowed."
              >
                <select
                  id="adjust-plan-week"
                  value={weekStart}
                  onChange={(e) => { setWeekStart(e.target.value); setErrors((er) => ({ ...er, week: undefined })) }}
                  disabled={submitting}
                  className="flex h-10 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {sortedWeeks.map((w) => (
                    <option key={w.week_start} value={w.week_start}>{weekOptionLabel(w)}</option>
                  ))}
                </select>
              </FormField>

              <div>
                <p className="mb-1.5 text-[11px] font-medium text-neutral-500">Merge into adjacent week</p>
                <div className="flex gap-2">
                  <Button
                    type="button" variant="secondary" size="sm" className="min-h-[44px] flex-1"
                    onClick={() => handleMerge('prev')}
                    disabled={!canMerge || !prevWeek || submitting || mergingDirection !== null}
                  >
                    {mergingDirection === 'prev' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                    &larr; Previous{prevWeek ? ` (${weekOptionLabel(prevWeek)})` : ''}
                  </Button>
                  <Button
                    type="button" variant="secondary" size="sm" className="min-h-[44px] flex-1"
                    onClick={() => handleMerge('next')}
                    disabled={!canMerge || !nextWeek || submitting || mergingDirection !== null}
                  >
                    {mergingDirection === 'next' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                    Next{nextWeek ? ` (${weekOptionLabel(nextWeek)})` : ''} &rarr;
                  </Button>
                </div>
                <p className="mt-1 text-[11px] text-neutral-400">
                  {canMerge
                    ? 'Folds this line’s quantity into the neighbouring week and locks both lines — the two fold into one line on the next Recalculate.'
                    : 'A capacity-gap line is unmet demand, not committed production, and cannot be merged.'}
                </p>
              </div>

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
              <Button type="submit" size="sm" className="min-h-[44px]" disabled={submitting || mergingDirection !== null}>
                {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Save Changes
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>,
    document.body,
  )
}
