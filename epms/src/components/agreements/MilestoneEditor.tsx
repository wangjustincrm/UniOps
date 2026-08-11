import { useEffect, useRef, type JSX } from 'react'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount } from '@/lib/utils'
import type { MilestoneRowIn } from '@/services/agreement'

function newMilestoneRow(): MilestoneRowIn {
  return { milestone_name: '', expected_timing: '', expected_amount: '', amount_pct: '' }
}

const cellInputClass =
  'h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400'

/**
 * `milestone` agreement stage editor. Rendered only by the parent when
 * `agreement_type === 'milestone'` — mirrors the backend rejection of
 * `milestones` on any other type
 * (epms-api/app/schemas/agreement.py::validate_milestones).
 */
export function MilestoneEditor(props: {
  rows: MilestoneRowIn[]
  notToExceed: string
  currency: string
  onChange: (rows: MilestoneRowIn[]) => void
  disabled?: boolean
}): JSX.Element {
  const { rows, notToExceed, currency, onChange, disabled } = props
  const nte = notToExceed ? Number(notToExceed) : null
  // ge=0 on the backend field technically allows an NTE of 0, but a zero
  // ceiling makes "% of NTE" a division by zero — treat it the same as
  // "no ceiling set" for the purpose of enabling the percentage column.
  const pctEnabled = nte !== null && nte > 0

  // `rows`/`onChange` as of the LATEST render, readable from inside the
  // debounced timeout below without making it a dependency (which would
  // defeat the debounce — see the effect's own comment). A plain
  // during-render assignment, not an effect: always current by the time the
  // timeout fires, including edits made to OTHER rows during the debounce
  // window that a stale closure would otherwise clobber.
  const rowsRef = useRef(rows)
  rowsRef.current = rows

  // Review finding 2 (round 1): the server stores whatever pair (%, Amount)
  // it's given and never re-derives one from the other once both are
  // populated (_resolve_milestone_amounts: "两个都给了就都存,不去纠正用户").
  // Chosen fix: recompute, not warn — every row that has a % SET gets its
  // Amount recomputed against the NEW ceiling whenever NTE changes, keeping
  // "% of NTE" true by construction instead of asking the user to notice a
  // discrepancy.
  //
  // Review finding 2 (round 2): this only works if "has a % set" reliably
  // means "the user typed a percentage" — but updateFromAmount used to
  // backfill amount_pct too, so a stage entered as a flat "$500" became
  // stored identically to one entered as "5%", and the NEXT NTE change would
  // silently overwrite that user's dollar figure. Fixed at the source in
  // updateFromAmount below (it no longer touches amount_pct at all), which
  // makes this effect's `r.amount_pct` check unambiguous: present now means
  // exactly "the user entered a percentage", nothing else.
  //
  // Debounced (round 2, cosmetic-but-reported-as-broken): notToExceed is a
  // plain text input one level up, so `nte` changes on every keystroke while
  // the user is typing a new ceiling — recomputing synchronously on each one
  // made every eligible row visibly flicker through intermediate values. The
  // 400ms delay lets a run of keystrokes settle before this fires once,
  // against the final value only. `prevNteRef` is only reassigned INSIDE the
  // timeout body, so a value that gets superseded before its timeout fires
  // never updates it — comparisons always run against the ceiling the user
  // actually stopped on, not an intermediate one that never really "landed".
  // Skips the initial mount (prevNteRef seeded from the first render's
  // value) so this doesn't fire before the user has changed anything, same
  // skip-on-mount shape as BudgetAccountCascade's prevDepartmentId guard.
  const prevNteRef = useRef(nte)
  useEffect(() => {
    const timer = setTimeout(() => {
      const prevNte = prevNteRef.current
      prevNteRef.current = nte
      if (prevNte === nte || nte === null || nte <= 0) return
      onChange(rowsRef.current.map((r) => {
        if (!r.amount_pct) return r
        const pct = Number(r.amount_pct)
        if (!Number.isFinite(pct)) return r
        return { ...r, expected_amount: String(Math.round(nte * pct) / 100) }
      }))
    }, 400)
    return () => clearTimeout(timer)
    // onChange intentionally excluded — same convention as BudgetAccountCascade.
    // rows/rowsRef intentionally excluded — that's the whole point of the ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nte])

  const update = (index: number, patch: Partial<MilestoneRowIn>) => {
    onChange(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  }

  // % of NTE -> Amount, computed live so the user never has to do the math
  // themselves (brief step 2: "输入 % 时按 Number(notToExceed) * pct / 100
  // 实时算出 Amount 并回填"). This is the ONLY place amount_pct is written —
  // see updateFromAmount below for why that matters.
  const updateFromPct = (index: number, pctStr: string) => {
    if (!pctEnabled) return
    const pct = Number(pctStr)
    const amount = pctStr !== '' && Number.isFinite(pct)
      ? String(Math.round((nte as number) * pct) / 100)
      : ''
    update(index, { amount_pct: pctStr, expected_amount: amount })
  }

  // Amount -> a read-only derived % shown next to it (see derivedPct below),
  // NOT a stored amount_pct. Review finding 2 (round 2): this used to
  // backfill amount_pct whenever pctEnabled, which made a row the user typed
  // as a flat dollar amount indistinguishable — in the stored data — from
  // one they typed as a percentage; the NTE-recompute effect above could
  // then silently rewrite that dollar figure on the next ceiling change.
  // Explicitly nulling amount_pct here (not just "not setting" it) also
  // un-links a row that previously WAS percentage-derived: editing its
  // Amount directly is the user overriding the percentage relationship, and
  // the row should stop being treated as percentage-derived from that point
  // on, not keep a stale % from before this edit.
  const updateFromAmount = (index: number, amountStr: string) => {
    update(index, { expected_amount: amountStr, amount_pct: null })
  }

  // Purely a render-time computation for rows with no stored amount_pct —
  // never written back via onChange/update, which is what makes it "not a
  // stored value" per the review comment. Returns null (render nothing) when
  // there's no ceiling to derive against or no amount to derive from.
  const derivedPct = (row: MilestoneRowIn): string | null => {
    if (!pctEnabled || row.amount_pct) return null
    const amt = row.expected_amount ? Number(row.expected_amount) : NaN
    if (!Number.isFinite(amt)) return null
    return (Math.round((amt / (nte as number)) * 100 * 100) / 100).toFixed(2)
  }

  const addRow = () => onChange([...rows, newMilestoneRow()])

  // MRP forecast's undefined.id crash (project_uniops_mrp_forecast_remove_row_crash)
  // was exactly this shape: an index used against the array without first
  // checking the row it points to still exists. Guard before touching it.
  const removeRow = (index: number) => {
    const row = rows[index]
    if (!row) return
    onChange(rows.filter((_, i) => i !== index))
  }

  const total = rows.reduce((sum, r) => sum + (r.expected_amount ? Number(r.expected_amount) : 0), 0)

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto rounded-lg border border-neutral-200">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th className="w-8 px-2 py-2.5 text-left text-xs font-semibold text-neutral-400">#</th>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-neutral-600">
                Stage name <span className="text-danger-600">*</span>
              </th>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-neutral-600">Timing</th>
              <th className="w-32 px-3 py-2.5 text-right text-xs font-semibold text-neutral-600">Amount</th>
              <th className="w-28 px-3 py-2.5 text-right text-xs font-semibold text-neutral-600">% of NTE</th>
              <th className="w-8 px-2 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const dp = derivedPct(row)
              return (
              <tr
                key={i}
                className={cn('border-b border-neutral-100 last:border-0', i % 2 === 1 ? 'bg-neutral-50/50' : 'bg-white')}
              >
                <td className="px-2 py-2 text-xs text-neutral-400">{i + 1}</td>
                <td className="px-3 py-2">
                  <input
                    type="text"
                    value={row.milestone_name}
                    disabled={disabled}
                    onChange={(e) => update(i, { milestone_name: e.target.value })}
                    placeholder="e.g. Design sign-off"
                    className={cellInputClass}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    type="text"
                    value={row.expected_timing ?? ''}
                    disabled={disabled}
                    onChange={(e) => update(i, { expected_timing: e.target.value })}
                    placeholder="e.g. Within 1 week after contract signing"
                    className={cellInputClass}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={row.expected_amount ?? ''}
                    disabled={disabled}
                    onChange={(e) => updateFromAmount(i, e.target.value)}
                    className={cn(cellInputClass, 'text-right')}
                  />
                  {/* Read-only, computed at render time, never stored — see
                      derivedPct's comment. Only shown for a row the user
                      defined in dollars (no amount_pct of its own). */}
                  {dp !== null && (
                    <p className="mt-0.5 text-right text-[10px] text-neutral-400">≈ {dp}% of NTE</p>
                  )}
                </td>
                <td className="px-3 py-2">
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step="0.01"
                    value={row.amount_pct ?? ''}
                    disabled={disabled || !pctEnabled}
                    onChange={(e) => updateFromPct(i, e.target.value)}
                    className={cn(cellInputClass, 'text-right')}
                  />
                </td>
                <td className="px-2 py-2">
                  <button
                    type="button"
                    onClick={() => removeRow(i)}
                    disabled={disabled}
                    className="flex h-7 w-7 items-center justify-center rounded text-neutral-300 hover:bg-danger-50 hover:text-danger-500 disabled:cursor-not-allowed disabled:opacity-30"
                    aria-label={`Remove stage ${i + 1}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
              )
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-sm text-neutral-400">
                  No stages yet — add one below.
                </td>
              </tr>
            )}
          </tbody>
        </table>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-neutral-200 bg-neutral-50 px-3 py-2.5">
          <Button type="button" variant="ghost" size="sm" onClick={addRow} disabled={disabled}>
            <Plus className="h-3.5 w-3.5" />
            Add Stage
          </Button>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-neutral-500">Total</span>
            <span className="amount font-semibold text-neutral-900">{formatAmount(total, currency)}</span>
            {nte !== null && (
              <span className={cn('font-medium', total > nte ? 'text-danger-600' : 'text-neutral-400')}>
                / {formatAmount(nte, currency)} NTE
              </span>
            )}
          </div>
        </div>
      </div>

      {!pctEnabled && (
        <p className="text-xs text-neutral-500">
          Set a not-to-exceed ceiling on the agreement to enter percentages
        </p>
      )}
    </div>
  )
}
