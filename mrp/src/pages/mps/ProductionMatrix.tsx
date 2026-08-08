// ProductionMatrix — read-only product x plan-month matrix for the
// Production Plan / MPS page (Production Plan Matrix Task 4). Reshapes the
// run's flat `MpsLine[]` into rows = products (each with three sub-rows
// Demand / Available / Planned), columns = distinct `plan_month`. This is a
// bespoke, purpose-built display table — NOT the editable `MatrixGrid`
// (@/components/MatrixGrid) that Sales Forecast uses: there is no
// paste/undo/keyboard-nav here, only aggregation plus a click-to-adjust
// affordance on Planned cells. T5 mounted this in ProductionPlanPage.tsx in
// place of the old CapacityBars.tsx + MpsLineTable.tsx (both retired).
//
// Sticky pattern ported verbatim from MatrixGrid.tsx's own header comment
// (itself the Sales Forecast sticky fix): ONE `overflow-auto` container
// carrying BOTH axes, not a nested overflow-x/overflow-y split — setting
// only one axis's overflow to a non-visible value forces the UA to compute
// the OTHER axis as `auto` too (CSS overflow spec), which silently produces
// two independent scroll containers, each becoming the "nearest scrolling
// ancestor" for a different axis of position:sticky — breaking the sticky
// thead/first-column's containing-block resolution. One container with both
// axes explicit removes the ambiguity.
import { useMemo } from 'react'
import { Lock, AlertTriangle, Clock } from 'lucide-react'
import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { MaterialOption } from '@/lib/materials'
import type { MpsLine } from './mpsApi'

// Sensible default in-container scroll cap for now — the brief notes the
// parent may pass an explicit height later (mirroring SalesForecastPage's
// measured-to-viewport `gridHeight`); until then this keeps the table
// self-contained and scrollable without growing the page unboundedly.
const MAX_HEIGHT = 560

// Product column / Metric column are both part of the single sticky-left
// block (the brief's "sticky first column" plus a Metric column that must
// stay legible while scrolling months horizontally). Fixed pixel widths so
// the Metric column's `left` offset can be a matching Tailwind arbitrary
// value below — see the header/body cells.
const PRODUCT_COL_WIDTH = 176 // px, Tailwind w-44

interface MatrixCell {
  demand: number
  available: number
  planned: number
  gap: boolean
  locked: boolean
  /** Production Lead Time (mrp08): true if any underlying line couldn't be
   *  pushed back the run's full `production_lead_months` before its demand
   *  month (clamped at "now"). Display-only marker on the Planned row —
   *  gap (red) takes precedence when a cell is both. */
  shortfall: boolean
  demandMonths: string[]
  /** The underlying MpsLine[] this cell aggregates — handed back verbatim
   *  to onAdjustCell so the caller (AdjustDrawer, per T5) knows exactly
   *  which lines a click on this cell refers to. */
  cellLines: MpsLine[]
}

function emptyCell(): MatrixCell {
  return { demand: 0, available: 0, planned: 0, gap: false, locked: false, shortfall: false, demandMonths: [], cellLines: [] }
}

function cellKey(materialCode: string, planMonth: string): string {
  return `${materialCode}::${planMonth}`
}

interface ProductionMatrixProps {
  lines: MpsLine[]
  /** code -> MaterialOption, for the Product column's name (not part of
   *  MpsLineResponse itself — see mpsApi.ts / ProductionPlanPage.tsx for how
   *  this is built from mdm-api's materials master). A lookup miss falls
   *  back to showing the bare code. */
  materialsByCode: Map<string, MaterialOption>
  /** Product codes with NO approved BOM yet (Continuous Sales Forecast Task
   *  9, spec §8b) — sourced from mdm-api's /boms/exist by the page (see
   *  bomStatusApi.ts), same batch call the Sales Forecast grid uses.
   *  Display only: 1B never explodes BOMs, so this changes nothing about
   *  adjust/lock/release. Optional: omitted, no product shows the badge. */
  noBomCodes?: ReadonlySet<string>
  /**
   * Display-unit scale (1 or 1000, mirroring MatrixGrid's `unitScale`) —
   * accepted for interface parity with the page's unit toggle, but NOT used
   * for any arithmetic here: every aggregate below is computed in KG straight
   * off the wire (demand_forecast/opening_stock/qty are always KG,
   * Decimal-as-string), and unit-aware display is entirely `formatValue`'s
   * job (the caller scales/labels consistently — see SalesForecastPage's
   * `gridFormatValue`/`gridUnitScale` pair for the established convention).
   */
  unitScale: number
  /** KG -> display string (unit-aware, caller-owned — e.g. kg or tonnes). */
  formatValue: (kg: number) => string
  /** Fired when a Planned cell with production is clicked (never while
   *  `readOnly`) — receives the exact MpsLine[] this cell aggregates. */
  onAdjustCell: (lines: MpsLine[]) => void
  readOnly: boolean
}

export function ProductionMatrix({
  lines,
  materialsByCode,
  noBomCodes,
  unitScale: _unitScale,
  formatValue,
  onAdjustCell,
  readOnly,
}: ProductionMatrixProps) {
  const products = useMemo(() => {
    const codes = [...new Set(lines.map((l) => l.material_code))].sort()
    return codes.map((code) => ({ code, name: materialsByCode.get(code)?.name ?? code }))
  }, [lines, materialsByCode])

  const planMonths = useMemo(
    () => [...new Set(lines.map((l) => l.plan_month))].sort(),
    [lines],
  )

  const cellMap = useMemo(() => {
    const map = new Map<string, MatrixCell>()
    for (const line of lines) {
      const key = cellKey(line.material_code, line.plan_month)
      let cell = map.get(key)
      if (!cell) {
        cell = emptyCell()
        map.set(key, cell)
      }
      cell.demand += Number(line.demand_forecast)
      cell.available += Number(line.opening_stock)
      cell.planned += Number(line.qty)
      if (line.capacity_gap) cell.gap = true
      if (line.locked_by_planner) cell.locked = true
      if (line.lead_shortfall) cell.shortfall = true
      if (!cell.demandMonths.includes(line.demand_month)) cell.demandMonths.push(line.demand_month)
      cell.cellLines.push(line)
    }
    for (const cell of map.values()) cell.demandMonths.sort()
    return map
  }, [lines])

  function getCell(materialCode: string, planMonth: string): MatrixCell {
    return cellMap.get(cellKey(materialCode, planMonth)) ?? emptyCell()
  }

  if (products.length === 0 || planMonths.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-12 text-center">
        <p className="text-sm text-neutral-500">No plan lines yet — generate a run to see the matrix here.</p>
      </div>
    )
  }

  return (
    <div
      className="overflow-auto rounded-lg border border-neutral-200"
      style={{ maxHeight: MAX_HEIGHT, maxWidth: '100%' }}
    >
      <table className="min-w-full border-collapse text-xs">
        <thead className="sticky top-0 z-20 bg-neutral-50">
          <tr>
            <th
              className="sticky left-0 z-30 border-b border-r border-neutral-200 bg-neutral-50 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600"
              style={{ width: PRODUCT_COL_WIDTH, minWidth: PRODUCT_COL_WIDTH }}
            >
              Product
            </th>
            <th
              className="sticky z-30 border-b border-r border-neutral-200 bg-neutral-50 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600"
              style={{ left: PRODUCT_COL_WIDTH, width: 90, minWidth: 90 }}
            >
              Metric
            </th>
            {planMonths.map((month) => (
              <th
                key={month}
                className="border-b border-r border-neutral-200 px-2 py-2 text-right text-[11px] font-semibold text-neutral-600 min-w-24"
              >
                {month}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {products.map((product) => (
            <ProductRows
              key={product.code}
              product={product}
              noBom={!!noBomCodes?.has(product.code)}
              planMonths={planMonths}
              getCell={getCell}
              formatValue={formatValue}
              onAdjustCell={onAdjustCell}
              readOnly={readOnly}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

type MetricRow = 'Demand' | 'Available' | 'Planned'
const METRIC_ROWS: MetricRow[] = ['Demand', 'Available', 'Planned']

function ProductRows({
  product,
  noBom,
  planMonths,
  getCell,
  formatValue,
  onAdjustCell,
  readOnly,
}: {
  product: { code: string; name: string }
  noBom: boolean
  planMonths: string[]
  getCell: (materialCode: string, planMonth: string) => MatrixCell
  formatValue: (kg: number) => string
  onAdjustCell: (lines: MpsLine[]) => void
  readOnly: boolean
}) {
  return (
    <>
      {METRIC_ROWS.map((metric, i) => (
        <tr key={metric} className="border-b border-neutral-100 last:border-0">
          {i === 0 && (
            <td
              rowSpan={METRIC_ROWS.length}
              className="sticky left-0 z-10 border-b border-r border-neutral-200 bg-white px-3 py-1.5 align-top text-left font-medium text-neutral-800"
              style={{ width: PRODUCT_COL_WIDTH, minWidth: PRODUCT_COL_WIDTH }}
            >
              <span className="flex items-center gap-1.5">
                <span className="truncate font-mono text-xs">{product.code}</span>
                {noBom && (
                  // Badge (packages/shell) doesn't spread rest props onto its
                  // <span> — a `title` passed directly to it is silently
                  // dropped. Wrap it in a plain span instead (matches the old
                  // MpsLineTable.tsx convention).
                  <span title="No approved BOM yet — Phase 1C material calc will skip this product">
                    <Badge variant="warning">No BOM</Badge>
                  </span>
                )}
              </span>
              {product.name !== product.code && (
                <span className="block truncate text-[11px] font-normal text-neutral-400">{product.name}</span>
              )}
            </td>
          )}
          <td
            className="sticky z-10 border-b border-r border-neutral-200 bg-neutral-50 px-3 py-1.5 text-left text-[11px] font-medium text-neutral-500"
            style={{ left: PRODUCT_COL_WIDTH, width: 90, minWidth: 90 }}
          >
            {metric}
          </td>
          {planMonths.map((month) => (
            <MetricCell
              key={month}
              metric={metric}
              cell={getCell(product.code, month)}
              formatValue={formatValue}
              onAdjustCell={onAdjustCell}
              readOnly={readOnly}
            />
          ))}
        </tr>
      ))}
    </>
  )
}

function MetricCell({
  metric,
  cell,
  formatValue,
  onAdjustCell,
  readOnly,
}: {
  metric: MetricRow
  cell: MatrixCell
  formatValue: (kg: number) => string
  onAdjustCell: (lines: MpsLine[]) => void
  readOnly: boolean
}) {
  const hasData = cell.cellLines.length > 0

  // The three metric sub-rows get distinct subtle backgrounds so a planner
  // can tell them apart at a glance while scrolling months horizontally —
  // applied to every cell in the row (not just ones with data) so the row
  // reads as a solid band. Planned's gap/shortfall highlighting below
  // overrides this base tint on the cells that need it.
  if (metric === 'Demand') {
    return (
      <td className="h-10 border-b border-r border-neutral-200 bg-neutral-50 px-2 text-right font-mono text-neutral-700">
        {hasData ? formatValue(cell.demand) : <span className="text-neutral-300">—</span>}
      </td>
    )
  }

  if (metric === 'Available') {
    return (
      <td className="h-10 border-b border-r border-neutral-200 bg-primary-50/40 px-2 text-right font-mono text-neutral-700">
        {hasData ? formatValue(cell.available) : <span className="text-neutral-300">—</span>}
      </td>
    )
  }

  // Planned — the only clickable row, and the actionable output (bold).
  // "Has production" = a non-zero planned qty; a cell that merely has lines
  // but nets to zero output (e.g. fully covered by available stock) renders
  // as empty, same as a cell with no lines at all — neither is something a
  // planner would click to adjust.
  const hasProduction = cell.planned > 0
  const title = cell.demandMonths.length > 0 ? `For ${cell.demandMonths.join(', ')} demand` : undefined
  // Gap (red, unmet demand) takes precedence over shortfall (amber,
  // produced later than the requested lead but still met) when a cell is
  // both — gap is the more severe condition a planner needs to see first.
  const showShortfall = cell.shortfall && !cell.gap

  if (!hasProduction) {
    return (
      <td className="h-10 border-b border-r border-neutral-200 bg-success-50/50 px-2 text-right font-mono text-neutral-300">—</td>
    )
  }

  const valueNode = (
    <span className="inline-flex items-center justify-end gap-1">
      {cell.gap && <AlertTriangle aria-hidden className="h-3 w-3 shrink-0 text-danger-600" />}
      {showShortfall && <Clock aria-hidden className="h-3 w-3 shrink-0 text-warning-600" />}
      {formatValue(cell.planned)}
      {cell.locked && <Lock aria-hidden className="h-3 w-3 shrink-0 text-neutral-400" />}
    </span>
  )

  const cellTitle = cell.gap
    ? `Capacity gap — unmet demand${title ? ` (${title})` : ''}`
    : showShortfall
      ? 'Produced later than the lead — no earlier capacity/time'
      : title

  return (
    <td
      className={cn(
        'h-10 border-b border-r border-neutral-200 px-2 text-right font-mono text-sm font-bold',
        cell.gap
          ? 'bg-danger-50 text-danger-700'
          : showShortfall
            ? 'bg-warning-50 text-warning-800'
            : 'bg-success-50/50 text-neutral-800',
      )}
    >
      {readOnly ? (
        <span title={cellTitle}>{valueNode}</span>
      ) : (
        <button
          type="button"
          onClick={() => onAdjustCell(cell.cellLines)}
          title={cellTitle}
          className="inline-flex h-full w-full items-center justify-end rounded px-1 hover:bg-primary-50 hover:text-primary-700 focus:outline-none focus:ring-1 focus:ring-primary-500"
        >
          {valueNode}
        </button>
      )}
    </td>
  )
}
