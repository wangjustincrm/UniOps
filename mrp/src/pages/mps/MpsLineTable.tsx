// Plan-line table — design §6.6 page 3. Checkbox-selectable rows (Lock /
// Unlock / Adjust… act on the current selection) plus a top action bar and
// an exception summary line surfacing capacity-gap lines, which are also
// styled as visibly blocked in the table itself (never colour alone — see
// CapacityBars.tsx's header note on the same a11y rule).
//
// This component owns row-selection state and knows nothing about mrp-api
// itself — the actual PATCH calls (lock/unlock/adjust) and the released-run
// 409 semantics live in ProductionPlanPage.tsx, which passes down plain
// callbacks (mirrors RuleDrawer.tsx's onSaved/notifySuccess split: children
// stay presentational, the page owns mutations).
import { useMemo, useState } from 'react'
import { ArrowUp, Lock, Unlock, PackageCheck, SquarePen, AlertTriangle } from 'lucide-react'
import { Button, Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { MaterialOption } from '@/lib/materials'
import type { MpsLine } from './mpsApi'

function formatQty(raw: string): string {
  const n = Number(raw)
  if (!Number.isFinite(n)) return raw
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

/** Whole-month difference between two 'YYYY-MM' strings, positive when
 *  `plan` is earlier than `demand` (a pre-build). Mirrors mps_engine.py's
 *  own `_month_index` arithmetic (year*12 + (month-1)), just read the
 *  other direction for display. */
function monthsEarlier(demandMonth: string, planMonth: string): number {
  const idx = (m: string) => {
    const [y, mo] = m.split('-').map(Number)
    return y * 12 + (mo - 1)
  }
  return idx(demandMonth) - idx(planMonth)
}

function productLabel(code: string, materialsByCode?: Map<string, MaterialOption>): { code: string; name: string | null } {
  const m = materialsByCode?.get(code)
  return { code, name: m?.name ?? null }
}

export function MpsLineTable({
  lines,
  materialsByCode,
  readOnly,
  canExecute,
  canRelease,
  actionBusy,
  onLockSelected,
  onUnlockSelected,
  onAdjustLine,
  onConfirmReleaseClick,
}: {
  lines: MpsLine[]
  /** code -> MaterialOption, for a friendlier Product column (name is not
   *  part of MpsLineResponse itself — see mpsApi.ts). Optional: a lookup
   *  miss just falls back to showing the bare code. */
  materialsByCode?: Map<string, MaterialOption>
  /** True once the run is released (immutable) — hides the checkbox column
   *  and the whole action bar; lines render read-only. */
  readOnly: boolean
  /** Gates Lock/Unlock/Adjust… (mrp.run.execute). */
  canExecute: boolean
  /** Gates Confirm & Release (mrp.proposal.confirm). */
  canRelease: boolean
  /** True while a lock/unlock/adjust/release call is in flight — disables
   *  the whole action bar so a second click can't race the first. */
  actionBusy: boolean
  onLockSelected: (lineIds: string[]) => void
  onUnlockSelected: (lineIds: string[]) => void
  /** Enabled only when exactly one row is selected — brief: "edit qty or
   *  plan_month for one line". */
  onAdjustLine: (line: MpsLine) => void
  onConfirmReleaseClick: () => void
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const gapCount = useMemo(() => lines.filter((l) => l.capacity_gap).length, [lines])
  const prebuildCount = useMemo(() => lines.filter((l) => l.is_prebuild).length, [lines])

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  function toggleAll() {
    setSelected((prev) => (prev.size === lines.length ? new Set() : new Set(lines.map((l) => l.id))))
  }

  const selectedIds = [...selected]
  const selectedLine = selectedIds.length === 1 ? lines.find((l) => l.id === selectedIds[0]) ?? null : null

  if (lines.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-12 text-center">
        <p className="text-sm text-neutral-500">No plan lines yet — generate a run to see them here.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Exception summary line — always visible when there's something to
          flag, not buried in a tooltip. */}
      {(gapCount > 0 || prebuildCount > 0) && (
        <div
          role={gapCount > 0 ? 'alert' : 'status'}
          className={cn(
            'flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border px-3 py-2 text-xs',
            gapCount > 0 ? 'border-danger-200 bg-danger-50 text-danger-800' : 'border-neutral-200 bg-neutral-50 text-neutral-600',
          )}
        >
          {gapCount > 0 && (
            <span className="flex items-center gap-1.5 font-medium">
              <AlertTriangle className="h-3.5 w-3.5" /> {gapCount} line{gapCount === 1 ? '' : 's'} blocked by capacity — resolve before releasing.
            </span>
          )}
          {prebuildCount > 0 && <span>{prebuildCount} line{prebuildCount === 1 ? '' : 's'} pre-built early.</span>}
        </div>
      )}

      {/* Top action bar */}
      {!readOnly && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-neutral-200 bg-white p-2">
          <span className="px-1 text-xs text-neutral-500">
            {selected.size > 0 ? `${selected.size} selected` : 'Select lines to lock, unlock or adjust'}
          </span>
          {canExecute && (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button" variant="secondary" size="sm" className="min-h-[44px]"
                onClick={() => onLockSelected(selectedIds)}
                disabled={selected.size === 0 || actionBusy}
              >
                <Lock className="h-3.5 w-3.5" /> Lock selected
              </Button>
              <Button
                type="button" variant="secondary" size="sm" className="min-h-[44px]"
                onClick={() => onUnlockSelected(selectedIds)}
                disabled={selected.size === 0 || actionBusy}
              >
                <Unlock className="h-3.5 w-3.5" /> Unlock selected
              </Button>
              <Button
                type="button" variant="secondary" size="sm" className="min-h-[44px]"
                onClick={() => selectedLine && onAdjustLine(selectedLine)}
                disabled={!selectedLine || actionBusy}
              >
                <SquarePen className="h-3.5 w-3.5" /> Adjust…
              </Button>
            </div>
          )}
          {canRelease && (
            <Button type="button" size="sm" className="min-h-[44px]" onClick={onConfirmReleaseClick} disabled={actionBusy}>
              <PackageCheck className="h-3.5 w-3.5" /> Confirm &amp; Release
            </Button>
          )}
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-neutral-200">
        <table className="min-w-full text-sm">
          <thead className="bg-neutral-50">
            <tr>
              {!readOnly && (
                <th className="w-10 border-b border-neutral-200 px-3 py-2">
                  <input
                    type="checkbox"
                    aria-label="Select all lines"
                    checked={selected.size === lines.length && lines.length > 0}
                    onChange={toggleAll}
                    className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500"
                  />
                </th>
              )}
              <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Product</th>
              <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Demand Mon</th>
              <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Plan Mon</th>
              <th className="border-b border-neutral-200 px-3 py-2 text-right text-[11px] font-semibold text-neutral-600">Qty</th>
              <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Pre-build</th>
              <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Shelf</th>
              <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Status</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => {
              const product = productLabel(line.material_code, materialsByCode)
              const n = line.is_prebuild ? monthsEarlier(line.demand_month, line.plan_month) : 0
              return (
                <tr
                  key={line.id}
                  className={cn(
                    'border-b border-neutral-100 last:border-0',
                    line.capacity_gap ? 'bg-danger-50/60' : selected.has(line.id) ? 'bg-primary-50/40' : 'odd:bg-white even:bg-neutral-50/50',
                  )}
                >
                  {!readOnly && (
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        aria-label={`Select ${line.material_code}`}
                        checked={selected.has(line.id)}
                        onChange={() => toggleOne(line.id)}
                        className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500"
                      />
                    </td>
                  )}
                  <td className="px-3 py-2">
                    <span className="block font-mono text-xs text-neutral-800">{product.code}</span>
                    {product.name && <span className="block truncate text-[11px] text-neutral-400">{product.name}</span>}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-600">{line.demand_month}</td>
                  <td className={cn('px-3 py-2 text-xs', line.plan_month !== line.demand_month ? 'font-medium text-primary-700' : 'text-neutral-600')}>
                    {line.plan_month}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-neutral-800">{formatQty(line.qty)}</td>
                  <td className="px-3 py-2">
                    {line.is_prebuild ? (
                      <span
                        className="inline-flex items-center gap-0.5 rounded-full bg-info-50 px-2 py-0.5 text-[11px] font-medium text-primary-700"
                        title={line.prebuild_reason ?? undefined}
                      >
                        <ArrowUp className="h-3 w-3" /> {n} mo
                      </span>
                    ) : (
                      <span className="text-neutral-300">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {line.shelf_life_ok ? (
                      <Badge variant="success">OK</Badge>
                    ) : (
                      <Badge variant="danger">GAP</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {line.capacity_gap ? (
                      <div className="flex flex-col gap-0.5">
                        <Badge variant="danger">Blocked</Badge>
                        {line.prebuild_reason && (
                          <span className="max-w-[220px] truncate text-[11px] text-danger-700" title={line.prebuild_reason}>
                            {line.prebuild_reason}
                          </span>
                        )}
                      </div>
                    ) : line.locked_by_planner ? (
                      <Badge variant="info">Locked</Badge>
                    ) : (
                      <span className="text-neutral-300">—</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
