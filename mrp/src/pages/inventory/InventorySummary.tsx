// The summary strip above the batch list.
//
// Scoped to whatever the filters currently match — search, material class,
// status — not to the whole warehouse, and computed server-side over every
// matching row rather than over the page on screen.
//
// ★ It arrives in the SAME response as the rows it summarises. Two endpoints
// taking the same filters drift apart, and a headline disagreeing with the
// table beneath it is a discrepancy nobody can explain and nothing reports —
// which has already happened once on this page (the aging cards).
//
// ★ ONE ROW PER UNIT. The warehouse holds kilograms, pieces, each, rolls and
// centipoise; packaging alone spans five of them. Adding them together
// produces a number that describes nothing. Raw ingredients are all KGM, so
// the default view is a single row and reads like a plain dashboard.
import { AlertTriangle, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { qty, type InventorySummaryLine } from './inventoryApi'

/** Add whole days to a 'YYYY-MM-DD' on a UTC clock — never through the local
 *  timezone, which shifts a date-only value by a day. */
function shiftIso(iso: string, days: number): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d) + days * 86_400_000).toISOString().slice(0, 10)
}

export function InventorySummary({
  summary, asOf, warningDays, isLoading, expiringFilterActive, onToggleExpiring,
}: {
  summary: InventorySummaryLine[]
  asOf: string | undefined
  warningDays: number
  isLoading: boolean
  expiringFilterActive: boolean
  /** Called with the exact window the server used, so clicking the tile shows
   *  the same rows it counted. Null clears the filter. */
  onToggleExpiring: (window: { after: string; before: string } | null) => void
}) {
  if (isLoading) {
    return (
      <div className="rounded-lg border border-neutral-200 bg-white p-4">
        <p className="text-xs text-neutral-400">Loading totals…</p>
      </div>
    )
  }
  if (summary.length === 0) {
    return null
  }

  const multiUnit = summary.length > 1

  return (
    <div className="rounded-lg border border-neutral-200 bg-white">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-neutral-100 px-4 py-2">
        <h2 className="text-sm font-semibold text-neutral-900">
          Totals for what you are looking at
        </h2>
        <span className="text-[11px] text-neutral-400">
          every matching row, not just this page
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full">
          <thead>
            <tr className="text-[11px] uppercase tracking-wide text-neutral-500">
              {/* The unit column only earns its space when there is more than
                  one unit in scope; with a single unit it rides along with the
                  numbers instead. */}
              {multiUnit && <th className="px-4 py-2 text-left">Unit</th>}
              <th className="px-4 py-2 text-right">Total</th>
              <th className="px-4 py-2 text-right">Available</th>
              <th className="px-4 py-2 text-right">Expired</th>
              <th className="px-4 py-2 text-right">Blocked</th>
              <th className="px-4 py-2 text-right">
                Expiring in {warningDays} days
              </th>
            </tr>
          </thead>
          <tbody>
            {summary.map((line) => {
              const unit = line.uom ?? 'no unit'
              const expiring = Number(line.expiring_soon_qty)
              return (
                <tr key={unit} className="border-t border-neutral-100">
                  {multiUnit && (
                    <td className="px-4 py-3 text-xs font-medium text-neutral-600">
                      {line.uom ?? (
                        // 495 lots belong to materials the master has no row
                        // for. Saying so beats an empty cell that reads as a
                        // rendering fault.
                        <span className="text-neutral-400">no unit on file</span>
                      )}
                    </td>
                  )}
                  <Metric value={line.total_qty} unit={unit} showUnit={!multiUnit}
                    sub={`${line.batches.toLocaleString('en-US')} batches · ${line.lots.toLocaleString('en-US')} lots`} />
                  <Metric value={line.available_qty} unit={unit} showUnit={!multiUnit}
                    tone="good" />
                  <Metric value={line.expired_qty} unit={unit} showUnit={!multiUnit}
                    tone={Number(line.expired_qty) > 0 ? 'bad' : undefined} />
                  <Metric value={line.blocked_qty} unit={unit} showUnit={!multiUnit}
                    tone={Number(line.blocked_qty) > 0 ? 'bad' : undefined} />
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      disabled={!asOf || (expiring === 0 && !expiringFilterActive)}
                      aria-pressed={expiringFilterActive}
                      onClick={() => {
                        if (!asOf) return
                        onToggleExpiring(expiringFilterActive ? null : {
                          // The server counted `expiry > as_of` and
                          // `expiry < as_of + N`, and both API bounds are
                          // exclusive — so this window is the same set the
                          // tile counted, not an approximation of it.
                          after: asOf,
                          before: shiftIso(asOf, warningDays),
                        })
                      }}
                      className={cn(
                        'rounded-md px-2 py-1 text-right transition-colors',
                        expiringFilterActive && 'bg-warning-100 ring-1 ring-warning-500',
                        expiring > 0 && !expiringFilterActive && 'hover:bg-warning-50',
                        expiring === 0 && !expiringFilterActive && 'cursor-default',
                      )}
                      title={expiring > 0
                        ? (expiringFilterActive
                          ? 'Showing only these — click to clear'
                          : 'Show only the batches expiring in this window')
                        : undefined}
                    >
                      <span className={cn('block text-lg font-semibold tabular-nums',
                        expiring > 0 ? 'text-warning-800' : 'text-neutral-400')}>
                        {qty(line.expiring_soon_qty)}
                        {!multiUnit && (
                          <span className="ml-1 text-xs font-normal text-neutral-400">{unit}</span>
                        )}
                      </span>
                      <span className="block text-[11px] text-neutral-500">
                        {line.expiring_soon_batches.toLocaleString('en-US')} batches
                      </span>
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="flex items-start gap-1.5 border-t border-neutral-100 px-4 py-2 text-[11px] text-neutral-500">
        <Info aria-hidden className="mt-0.5 h-3 w-3 shrink-0 text-neutral-400" />
        <span>
          These overlap and do not add up to the total — expired stock is usually
          blocked too, and stock expiring soon is still available today.
          {' '}<strong>Blocked</strong> is the warehouse's own hold (QLT_STS 01);
          material still under inspection is not counted as blocked.
          {asOf && <> Shelf life is measured from {asOf}.</>}
        </span>
      </p>

      {summary.length > 1 && (
        <p className="flex items-start gap-1.5 border-t border-neutral-100 px-4 py-2 text-[11px] text-neutral-500">
          <AlertTriangle aria-hidden className="mt-0.5 h-3 w-3 shrink-0 text-neutral-400" />
          <span>
            This selection spans {summary.length} units of measure, so the totals
            are shown per unit — adding kilograms to pieces would produce a number
            that means nothing.
          </span>
        </p>
      )}
    </div>
  )
}

function Metric({
  value, unit, showUnit, tone, sub,
}: {
  value: string
  unit: string
  showUnit: boolean
  tone?: 'good' | 'bad'
  sub?: string
}) {
  return (
    <td className="px-4 py-3 text-right">
      <span className={cn(
        'block text-lg font-semibold tabular-nums',
        tone === 'good' && 'text-success-700',
        tone === 'bad' && 'text-danger-600',
        !tone && 'text-neutral-900',
      )}>
        {qty(value)}
        {showUnit && <span className="ml-1 text-xs font-normal text-neutral-400">{unit}</span>}
      </span>
      {sub && <span className="block text-[11px] text-neutral-500">{sub}</span>}
    </td>
  )
}
