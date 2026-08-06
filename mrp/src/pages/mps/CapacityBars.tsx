// Per-month capacity occupancy bars — design §6.6 page 3, top of the
// Production Plan page. One bar per month present in `capacity_occupancy`
// (GET /mps/runs/{id} — see mpsApi.ts's header: only months with at least
// one placed non-gap line appear, computed fresh on every read, never a
// stored snapshot).
//
// Accessibility: a bar at or over its limit is never conveyed by colour
// alone — a "Full"/"Over by …" text label always accompanies the colour
// change, per the design spec and this repo's own a11y bar for status
// (see feedback_uniops_ui_audit_2026_08 in project memory: colour-only
// signalling is the weakest-scoring UI pattern in this codebase).
import { cn } from '@/lib/utils'
import type { CapacityOccupancyMonth } from './mpsApi'

/** Planning UOM is KG (project convention). Displayed as tonnes (kg/1000)
 *  once the *limit* (or, absent a limit, the used qty) reaches 1000 kg, so
 *  a used/limit pair always shares one unit instead of mixing "850 kg /
 *  1.2 t". */
function formatQtyPair(usedKg: number, maxKg: number | null): { used: string; max: string; unit: string; divisor: number } {
  const basisKg = maxKg ?? usedKg
  const asTonnes = basisKg >= 1000
  const unit = asTonnes ? 't' : 'kg'
  const divisor = asTonnes ? 1000 : 1
  const fmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: asTonnes ? 2 : 1 })
  return {
    used: fmt.format(usedKg / divisor),
    max: maxKg === null ? '—' : fmt.format(maxKg / divisor),
    unit,
    divisor,
  }
}

/** Formats a raw kg delta using an already-decided unit/divisor, so an
 *  "Over by …" label always matches the unit of the used/limit pair it sits
 *  beside — recomputing the unit independently from the (often much
 *  smaller) overage alone could pick 'kg' next to a 't' pair. */
function formatDelta(deltaKg: number, unit: string, divisor: number): string {
  const fmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: unit === 't' ? 2 : 1 })
  return fmt.format(deltaKg / divisor)
}

interface MeterState {
  pct: number | null // null = no limit to measure against
  over: boolean
  full: boolean
}

function meterState(used: number, max: number | null): MeterState {
  if (max === null || max <= 0) return { pct: null, over: false, full: false }
  const pct = (used / max) * 100
  return { pct, over: pct > 100, full: pct >= 99.95 && pct <= 100 }
}

function barFillClass(state: MeterState): string {
  if (state.pct === null) return 'bg-neutral-300'
  if (state.over) return 'bg-danger-600'
  if (state.full) return 'bg-warning-500'
  return 'bg-primary-600'
}

function MonthBar({ month }: { month: CapacityOccupancyMonth }) {
  const usedQty = Number(month.used_qty)
  const maxQty = month.max_output_qty === null ? null : Number(month.max_output_qty)
  const qty = formatQtyPair(usedQty, maxQty)
  const qtyState = meterState(usedQty, maxQty)
  const skuState = meterState(month.used_sku_count, month.max_sku_count)

  return (
    <div
      className={cn(
        'flex min-w-[190px] flex-1 flex-col gap-2 rounded-lg border bg-white p-3',
        qtyState.over || skuState.over
          ? 'border-danger-300'
          : qtyState.full || skuState.full
            ? 'border-warning-300'
            : 'border-neutral-200',
      )}
    >
      <div className="text-xs font-semibold text-neutral-700">{month.month}</div>

      {/* Output qty meter */}
      <div className="flex flex-col gap-1">
        <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-100" role="img" aria-label={`Output ${qty.used} of ${qty.max} ${qty.unit}`}>
          <div
            className={cn('h-full rounded-full transition-[width]', barFillClass(qtyState))}
            style={{ width: `${qtyState.pct === null ? 100 : Math.min(qtyState.pct, 100)}%` }}
          />
        </div>
        <div className="flex items-center justify-between gap-2 text-[11px] text-neutral-600">
          <span>{qty.used} / {qty.max === '—' ? 'no limit' : `${qty.max} ${qty.unit}`}</span>
          {qtyState.over && <span className="font-semibold text-danger-700">Over by {formatDelta(usedQty - (maxQty ?? 0), qty.unit, qty.divisor)} {qty.unit}</span>}
          {!qtyState.over && qtyState.full && <span className="font-semibold text-warning-700">Full</span>}
        </div>
      </div>

      {/* SKU count meter */}
      <div className="flex items-center justify-between gap-2 text-[11px] text-neutral-600">
        <span>
          {month.used_sku_count} / {month.max_sku_count ?? '∞'} SKUs
        </span>
        {skuState.over && <span className="font-semibold text-danger-700">Over limit</span>}
        {!skuState.over && skuState.full && <span className="font-semibold text-warning-700">Full</span>}
      </div>
    </div>
  )
}

export function CapacityBars({ occupancy }: { occupancy: CapacityOccupancyMonth[] }) {
  if (occupancy.length === 0) {
    return (
      <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-neutral-300 py-8 text-center">
        <p className="text-sm text-neutral-500">No capacity usage yet — no lines have been placed into a month.</p>
      </div>
    )
  }

  const months = [...occupancy].sort((a, b) => a.month.localeCompare(b.month))

  return (
    <div className="flex flex-wrap gap-3">
      {months.map((m) => (
        <MonthBar key={m.month} month={m} />
      ))}
    </div>
  )
}
