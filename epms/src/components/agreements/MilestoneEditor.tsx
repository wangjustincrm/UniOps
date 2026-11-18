import { useEffect, useRef, useState, type JSX } from 'react'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount } from '@/lib/utils'
import type { MilestoneRowIn } from '@/services/agreement'

import { Input, TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'
function newMilestoneRow(): MilestoneRowIn {
  return { milestone_name: '', expected_timing: '', expected_amount: '', amount_pct: '' }
}

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

  // Review finding 2 history — recompute was tried and abandoned, in favor
  // of a warning, across three rounds:
  //
  // Round 1: the server stores whatever pair (%, Amount) it's given and
  // never re-derives one from the other once both are populated
  // (_resolve_milestone_amounts: "两个都给了就都存,不去纠正用户"). Tried
  // "recompute, not warn" — every row with a % set got its Amount
  // recomputed against the new ceiling whenever NTE changed.
  //
  // Round 2: that only works if "has a % set" reliably means "the user
  // typed a percentage" — updateFromAmount was backfilling amount_pct too,
  // so a flat "$500" entry became indistinguishable from a "5%" entry, and
  // the recompute would silently rewrite the dollar figure. Fixed
  // updateFromAmount to stop backfilling amount_pct (see below) — which
  // made the recompute's `r.amount_pct` check unambiguous WITHIN A SINGLE
  // SESSION, and it was re-verified working for that case.
  //
  // Round 3 (why recompute was removed entirely): round 2's own evidence
  // proved recompute unsound ACROSS a save boundary anyway — the backend
  // itself backfills the missing half of the pair on save
  // (_resolve_milestone_amounts, same function), so a dollar-only stage
  // that gets saved and reloaded comes back from the schedule endpoint with
  // a real, non-null, SERVER-derived amount_pct. From that point on this
  // component cannot tell "the user typed a %" apart from "the server
  // derived one" — both look identical in `rows`. A later NTE change would
  // then recompute (and silently overwrite) a figure the user had actually
  // typed as a dollar amount, reintroducing exactly the bug round 2 fixed,
  // just one edit-session later. Making this sound would need an explicit
  // source-of-entry field, which the coordinator ruled out as a stored-data
  // change with no backend counterpart, not worth a schema round for a
  // warning that costs nothing. So: no more recomputing/rewriting numbers
  // the user typed — see the warning banner in the JSX below instead, driven
  // by staleAfterNteChange.
  const [staleAfterNteChange, setStaleAfterNteChange] = useState(false)
  const prevNteRef = useRef(nte)
  useEffect(() => {
    const prevNte = prevNteRef.current
    prevNteRef.current = nte
    if (prevNte === nte) return
    // Only worth flagging if some row actually has a stored % that could now
    // be stale — a purely dollar-defined stage list has nothing to warn
    // about. Reads `rows` as of the render where nte changed; does not write
    // to any row, so no stale-closure risk the way the old recompute had.
    if (rows.some((r) => r.amount_pct)) setStaleAfterNteChange(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nte])

  const update = (index: number, patch: Partial<MilestoneRowIn>) => {
    // Editing any stage is the user's acknowledgment of the warning (per the
    // coordinator: visible until the user edits a stage or saves — saving
    // navigates away, which unmounts this component, so that half needs no
    // separate handling here).
    setStaleAfterNteChange(false)
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

  // Amount -> a derived % shown in the % field (see derivedPct below),
  // NOT a stored amount_pct. Review finding 2 (round 2): this used to
  // backfill amount_pct whenever pctEnabled, which made a row the user typed
  // as a flat dollar amount indistinguishable — in the stored data — from
  // one they typed as a percentage. Explicitly nulling amount_pct here (not
  // just "not setting" it) also un-links a row that previously WAS
  // percentage-derived: editing its Amount directly is the user overriding
  // the percentage relationship, and the row should stop being treated as
  // percentage-derived from that point on, not keep a stale % from before
  // this edit.
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
      <div className="overflow-x-auto rounded-lg border border-line">
        <Table density="compact" className="min-w-[640px]">
          <THead>
            <Tr className="bg-surface-subtle">
              <Th className="w-8">#</Th>
              <Th>
                Stage name <span className="text-danger-fg">*</span>
              </Th>
              <Th>Timing</Th>
              <Th align="right" className="w-32 ">Amount</Th>
              <Th align="right" className="w-28 ">% of NTE</Th>
              <Th className="w-8" />
            </Tr>
          </THead>
          <TBody>
            {rows.map((row, i) => {
              const dp = derivedPct(row)
              return (
              <Tr
                key={i}
                className={cn('border-b border-line-subtle last:border-0', i % 2 === 1 ? 'bg-surface-subtle/50' : 'bg-surface')}
              >
                <Td className="text-xs text-fg-muted">{i + 1}</Td>
                <Td>
                  <Input
                    type="text"
                    value={row.milestone_name}
                    disabled={disabled}
                    onChange={(e) => update(i, { milestone_name: e.target.value })}
                    placeholder="e.g. Design sign-off"
                    inputSize="sm"
                  />
                </Td>
                <Td>
                  <Input
                    type="text"
                    value={row.expected_timing ?? ''}
                    disabled={disabled}
                    onChange={(e) => update(i, { expected_timing: e.target.value })}
                    placeholder="e.g. Within 1 week after contract signing"
                    inputSize="sm"
                  />
                </Td>
                <Td>
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={row.expected_amount ?? ''}
                    disabled={disabled}
                    onChange={(e) => updateFromAmount(i, e.target.value)}
                    inputSize="sm" className="text-right"
                  />
                </Td>
                <Td>
                  <Input
                    type="number"
                    min={0}
                    max={100}
                    step="0.01"
                    // A row entered as an amount shows its % in this field the
                    // way a row entered as a % shows its amount (AGR-004 "the
                    // other is derived", ISS-0130) — but from derivedPct, at
                    // render time, never stored: the row stays dollar-defined,
                    // follows an NTE change, and the server derives the same
                    // % on save. Typing here makes it a %-defined row.
                    value={row.amount_pct || dp || ''}
                    disabled={disabled || !pctEnabled}
                    onChange={(e) => updateFromPct(i, e.target.value)}
                    inputSize="sm" className="text-right"
                  />
                </Td>
                <Td>
                  <Button variant="danger-ghost" size="icon-xs"
                    type="button"
                    onClick={() => removeRow(i)}
                    disabled={disabled}
                   
                    aria-label={`Remove stage ${i + 1}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </Td>
              </Tr>
              )
            })}
            {rows.length === 0 && (
              <Tr>
                <Td align="center" colSpan={6} className="text-sm text-fg-muted">
                  No stages yet — add one below.
                </Td>
              </Tr>
            )}
          </TBody>
        </Table>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-surface-subtle px-3 py-2.5">
          <Button type="button" variant="ghost" size="sm" onClick={addRow} disabled={disabled}>
            <Plus className="h-3.5 w-3.5" />
            Add Stage
          </Button>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-fg-muted">Total</span>
            <span className="amount font-semibold text-fg">{formatAmount(total, currency)}</span>
            {nte !== null && (
              <span className={cn('font-medium', total > nte ? 'text-danger-fg' : 'text-fg-muted')}>
                / {formatAmount(nte, currency)} NTE
              </span>
            )}
          </div>
        </div>
      </div>

      {staleAfterNteChange && (
        <div className="rounded-lg border border-warning-line bg-warning-soft px-4 py-3">
          <p className="text-sm text-warning-fg">
            The not-to-exceed ceiling changed. Stage amounts calculated from a percentage were calculated against the
            previous ceiling and may no longer match it — re-enter any percentage you want recalculated.
          </p>
        </div>
      )}

      {pctEnabled ? (
        <p className="text-xs text-fg-muted">Enter an amount or a %; the other is calculated.</p>
      ) : (
        <p className="text-xs text-fg-muted">
          Set a not-to-exceed ceiling on the agreement to enter percentages
        </p>
      )}
    </div>
  )
}
