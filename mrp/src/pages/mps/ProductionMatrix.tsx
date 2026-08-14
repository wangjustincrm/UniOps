// ProductionMatrix — read-only product x plan-WEEK matrix for the
// Production Plan / MPS page, grouped under collapsible month headers
// (weekly rework Task 9, design §5.1). Reshapes the run's flat `MpsLine[]`
// into rows = products (each with three sub-rows Demand / Available /
// Planned), columns = weeks (or one collapsed summary column per month the
// planner hasn't expanded). This is a bespoke, purpose-built display
// table — NOT the editable `MatrixGrid` (@/components/MatrixGrid) that
// Sales Forecast uses: there is no paste/undo/keyboard-nav here, only
// aggregation plus a click-to-adjust affordance on Planned cells.
//
// The column MODEL (which months/weeks exist, expand/collapse state) is
// pure logic split out into weekColumns.ts (verified by
// weekColumns.verify.ts, `npx tsx`) — this file only renders it. As of
// review round 1, "which weeks exist" comes ENTIRELY from the run's own
// `week_grid` (`GET /runs/{id}`, mrp-api's `_compute_week_grid`) via
// `buildWeekRefs` — never derived from `lines` and never synthesized here.
// A maintenance week has zero lines by construction (that is what "no
// production this week" means) and still needs a header to click; `lines`
// alone cannot answer "which weeks exist", only "which weeks have output".
//
// Two semantics this file must not get wrong (see mpsApi.ts's MpsLine for
// the full doc): `is_prebuild` means the plan week's OWNING MONTH precedes
// the demand's own bucket month, not "earlier than the target week" — for
// "is this line early", read `weeks_early`. Demand and Available stay
// MONTHLY (one value per product per month); only Planned is per-week.
//
// Demand/Available are snapshotted PER DEMAND MONTH and copied onto EVERY
// line of that demand month (mrp07) — weekly, one demand month spans
// several lines (several weeks, or a pre-build straddling two plan
// months), so summing per LINE inflates by however many lines share the
// snapshot. `aggregateLines` below dedups on `(column key, demand_month)`,
// mirroring `app/services/mps_export.py::build_mps_matrix_workbook`'s own
// `counted` set exactly (that fix already shipped once, in the export,
// Task 7 — review round 1 caught that this file needed the same fix on the
// other side of the wire; measured 480000/80000 against a true 120000/20000
// on a realistic 4-week-levelled run before the fix).
//
// Sticky pattern ported verbatim from MatrixGrid.tsx's own header comment
// (itself the Sales Forecast sticky fix): ONE `overflow-auto` container
// carrying BOTH axes, not a nested overflow-x/overflow-y split — setting
// only one axis's overflow to a non-visible value forces the UA to compute
// the OTHER axis as `auto` too (CSS overflow spec), which silently produces
// two independent scroll containers, each becoming the "nearest scrolling
// ancestor" for a different axis of position:sticky — breaking the sticky
// thead/first-column's containing-block resolution. One container with both
// axes explicit removes the ambiguity. The header is now TWO rows (month
// row + week row) instead of one, but still ONE sticky unit: `<thead>`
// itself carries `sticky top-0`, so both rows ride along together with no
// separate top-offset math for row 2 — see the `<thead>` element's own
// comment below for why.
import { useMemo, useState } from 'react'
import { Lock, AlertTriangle, Clock, ChevronDown, ChevronRight, Wrench } from 'lucide-react'
import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { MaterialOption } from '@/lib/materials'
import type { MpsLine, WeekGridEntry } from './mpsApi'
import { buildWeekColumns, buildWeekRefs, defaultExpandedMonths, type Column, type WeekColumn } from './weekColumns'

// Sensible default in-container scroll cap for now — the brief notes the
// parent may pass an explicit height later (mirroring SalesForecastPage's
// measured-to-viewport `gridHeight`); until then this keeps the table
// self-contained and scrollable without growing the page unboundedly.
const MAX_HEIGHT = 560

const EMPTY_STRING_SET: ReadonlySet<string> = new Set()

// Product column / Metric column are both part of the single sticky-left
// block (the brief's "sticky first column" plus a Metric column that must
// stay legible while scrolling weeks horizontally). Fixed pixel widths so
// the Metric column's `left` offset can be a matching Tailwind arbitrary
// value below — see the header/body cells.
const PRODUCT_COL_WIDTH = 176 // px, Tailwind w-44

const WEEK_COL_MIN_WIDTH = 76 // px — narrower than the old month columns; a week's Planned value is usually a smaller number

interface MatrixCell {
  demand: number
  available: number
  planned: number
  gap: boolean
  locked: boolean
  /** Production Lead Time (mrp08, now weeks): true if any underlying line
   *  couldn't be pushed back the run's full `production_lead_weeks` before
   *  its demand month (clamped at "now"). Display-only marker on the
   *  Planned row — gap (red) takes precedence when a cell is both. */
  shortfall: boolean
  demandMonths: string[]
  /** The underlying MpsLine[] this cell aggregates — handed back verbatim
   *  to onAdjustCell so the caller (AdjustDrawer) knows exactly which
   *  lines a click on this cell refers to. */
  cellLines: MpsLine[]
}

function emptyCell(): MatrixCell {
  return { demand: 0, available: 0, planned: 0, gap: false, locked: false, shortfall: false, demandMonths: [], cellLines: [] }
}

/** Aggregates `lines` into one MatrixCell per key from `keyFn` — the same
 *  accumulation logic serves both the month-grain map (Demand/Available,
 *  and Planned for a collapsed month's summary column) and the week-grain
 *  map (Planned for an expanded month's week columns); only the grouping
 *  key differs.
 *
 *  Two rules here mirror `app/services/mps_export.py::build_mps_matrix_workbook`
 *  exactly (review round 1):
 *
 *  - **Demand/Available dedup per `(key, demand_month)`, not per line.**
 *    `demand_forecast`/`opening_stock` are a snapshot taken ONCE per demand
 *    month and copied onto every line of that month — weekly, several
 *    lines commonly share a demand month (several weeks' worth of one
 *    month's levelled production, or a pre-build straddling two plan
 *    months), so summing per line multiplies the true figure by however
 *    many lines happen to land in the same cell. A `capacity_gap` line
 *    still counts toward this dedup (it still carries the real demand
 *    snapshot for its month) — excluding it would UNDER-report demand for
 *    the unmet portion, the opposite of the bug being fixed.
 *  - **Planned excludes `capacity_gap` lines' qty.** A gap line is an
 *    un-placed shortfall, never a booked production slot
 *    (`mps_engine.py`'s own docstring) — Task 7's review caught the export
 *    making this same mistake (summing a gap line's qty straight into
 *    "Planned"), and Task 8 fixed it there by giving Gap its own row. This
 *    matrix has no separate Gap row (out of Task 9's scope), so a gap
 *    contributes only to the `gap` flag, which still marks the cell red
 *    with an alert icon (see `PlannedCell`) — never to the numeric total. */
function aggregateLines(lines: MpsLine[], keyFn: (line: MpsLine) => string): Map<string, MatrixCell> {
  const map = new Map<string, MatrixCell>()
  const countedDemandMonths = new Map<string, Set<string>>()

  for (const line of lines) {
    const key = keyFn(line)
    let cell = map.get(key)
    if (!cell) {
      cell = emptyCell()
      map.set(key, cell)
    }

    if (line.capacity_gap) cell.gap = true
    else cell.planned += Number(line.qty)
    if (line.locked_by_planner) cell.locked = true
    if (line.lead_shortfall) cell.shortfall = true
    if (!cell.demandMonths.includes(line.demand_month)) cell.demandMonths.push(line.demand_month)
    cell.cellLines.push(line)

    let seen = countedDemandMonths.get(key)
    if (!seen) {
      seen = new Set()
      countedDemandMonths.set(key, seen)
    }
    if (!seen.has(line.demand_month)) {
      seen.add(line.demand_month)
      cell.demand += Number(line.demand_forecast)
      cell.available += Number(line.opening_stock)
    }
  }
  for (const cell of map.values()) cell.demandMonths.sort()
  return map
}

function monthCellKey(materialCode: string, month: string): string {
  return `${materialCode}::${month}`
}

function weekCellKey(materialCode: string, weekStart: string): string {
  return `${materialCode}::${weekStart}`
}

interface ProductionMatrixProps {
  lines: MpsLine[]
  /** The run's own week grid (`GET /runs/{id}`'s `week_grid`, mrp-api's
   *  `_compute_week_grid`) — the AUTHORITATIVE "which weeks exist" list,
   *  covering the run's declared horizon plus any month a pre-build line
   *  actually landed in, with every real week enumerated regardless of
   *  whether it carries a line. Every column in this matrix comes from
   *  this list via `buildWeekRefs`, never from `lines` — a maintenance
   *  week or a zero-net-demand month has zero lines by construction and
   *  would otherwise vanish from the axis. */
  weekGrid: WeekGridEntry[]
  /** week_start values of every week a capacity exception CLOSES — the
   *  grey tint + wrench icon on that week's header and Planned cell.
   *
   *  "Closed" is `capacityApi.closesWeek`, restated from the engine's own
   *  `mps_engine.py::_week_can_host`: `max_output_qty <= 0` OR
   *  `max_sku_count < 1`, factory scope, active. It used to be
   *  `max_output_qty === 0` alone, which left a week closed by a
   *  `Max SKUs / week` exception fully honoured by the engine and never
   *  tinted here.
   *
   *  **Round-1 review fix: this must NOT be derived from
   *  `capacity_occupancy`.** `_compute_capacity_occupancy`
   *  (mrp-api/app/api/v1/mps.py) builds its per-week map from the run's OWN
   *  LINES, and `_week_can_host` closes a `max_output_qty <= 0` week to all
   *  placement — so a maintenance week has ZERO lines by construction
   *  (mpsApi.ts's own `WeekGridEntry` doc already says this) and therefore
   *  NO occupancy entry, the moment the planner does the one thing the
   *  WeekDrawer save toast tells them to do (Recalculate). Occupancy cannot
   *  see what the engine enforces, precisely because enforcement there
   *  means "no lines to report". The exceptions list the page already
   *  fetches (`capacity-exceptions` query) has no such blind spot — it says
   *  what's CONFIGURED, not what got PLACED, so it stays true before AND
   *  after every recalculate. Optional: omitted, no week is ever tinted. */
  maintenanceWeekStarts?: ReadonlySet<string>
  /** True while the page's own capacity-exceptions query hasn't resolved
   *  yet, or came back an error — the tint is deliberately withheld rather
   *  than shown, so a stale/absent read never draws a WRONG grey/wrench on
   *  a week that may or may not actually be maintenance (same reasoning
   *  WeekDrawer's own loading gate uses, see that file). Optional: treated
   *  as `false` (i.e. `maintenanceWeekStarts` is trusted outright) when
   *  omitted, for callers that don't have a query to report. */
  maintenanceDataUnready?: boolean
  /** Fired when a WEEK column header is clicked (never for a collapsed
   *  month's summary column, which has no single week to attach a
   *  maintenance flag to) — opens WeekDrawer. Same click-to-open
   *  interaction the Planned cells already use for AdjustDrawer; see
   *  design §5.1's last bullet. Optional: omitted, week headers render as
   *  plain (non-interactive) text, same as before this prop existed. */
  onOpenWeekDrawer?: (week: WeekGridEntry) => void
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
  weekGrid,
  maintenanceWeekStarts,
  maintenanceDataUnready,
  onOpenWeekDrawer,
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

  // The known-weeks list handed to buildWeekColumns — a straight,
  // non-filtering conversion of the run's own week_grid (see this file's
  // header comment and buildWeekRefs's own doc for why `lines` never feeds
  // this: a maintenance week or a zero-net-demand month has zero lines by
  // construction and week_grid already enumerates its real weeks anyway).
  const weekRefs = useMemo(() => buildWeekRefs(weekGrid), [weekGrid])

  // See `maintenanceWeekStarts`'s own doc on the props interface above for
  // why this reads the caller-supplied exceptions set rather than deriving
  // anything from occupancy/lines itself. Withheld (empty) while the data
  // isn't ready, rather than trusting a possibly-stale/absent set.
  const maintenanceWeeks: ReadonlySet<string> =
    maintenanceDataUnready ? EMPTY_STRING_SET : (maintenanceWeekStarts ?? EMPTY_STRING_SET)

  const weekGridByStart = useMemo(() => {
    const m = new Map<string, WeekGridEntry>()
    for (const w of weekGrid) m.set(w.week_start, w)
    return m
  }, [weekGrid])

  const allMonthsOrdered = useMemo(
    () => [...new Set(weekRefs.map((w) => w.month))].sort(),
    [weekRefs],
  )

  // Default expand set is seeded once per mount (ProductionPlanPage remounts
  // this component with `key={run.id}` on every run switch, so this
  // correctly re-seeds per run rather than carrying stale expand state
  // across runs).
  const [expandedMonths, setExpandedMonths] = useState<Set<string>>(() => defaultExpandedMonths(allMonthsOrdered))

  function toggleMonth(month: string) {
    setExpandedMonths((prev) => {
      const next = new Set(prev)
      if (next.has(month)) next.delete(month)
      else next.add(month)
      return next
    })
  }

  const columns = useMemo(() => buildWeekColumns(weekRefs, expandedMonths), [weekRefs, expandedMonths])

  const orderedMonths = useMemo(() => {
    const seen = new Set<string>()
    const out: string[] = []
    for (const col of columns) {
      if (!seen.has(col.month)) {
        seen.add(col.month)
        out.push(col.month)
      }
    }
    return out
  }, [columns])

  const monthSpans = useMemo(() => {
    const spans = new Map<string, number>()
    for (const col of columns) spans.set(col.month, (spans.get(col.month) ?? 0) + 1)
    return spans
  }, [columns])

  const monthCellMap = useMemo(
    () => aggregateLines(lines, (l) => monthCellKey(l.material_code, l.plan_week_month)),
    [lines],
  )
  const weekCellMap = useMemo(
    () => aggregateLines(lines, (l) => weekCellKey(l.material_code, l.plan_week_start)),
    [lines],
  )

  function getMonthCell(materialCode: string, month: string): MatrixCell {
    return monthCellMap.get(monthCellKey(materialCode, month)) ?? emptyCell()
  }
  function getWeekCell(materialCode: string, weekStart: string): MatrixCell {
    return weekCellMap.get(weekCellKey(materialCode, weekStart)) ?? emptyCell()
  }
  /** Planned cell for one column: a real week column reads the week-grain
   *  map; a collapsed month's summary column reads the month-grain map
   *  (the same aggregate Demand/Available use), matching the pre-week
   *  monthly matrix's behaviour for a month the planner hasn't expanded. */
  function getPlannedCell(materialCode: string, col: Column): MatrixCell {
    return col.kind === 'week' ? getWeekCell(materialCode, col.week_start) : getMonthCell(materialCode, col.month)
  }

  if (products.length === 0 || orderedMonths.length === 0) {
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
        {/* The WHOLE <thead> is the sticky-top unit (not each row/cell
            individually) — same technique the pre-week single-row header
            used (see this file's header comment): a `position: sticky`
            table-header-group pins as one box, so both header rows ride
            along together with no separate offset math needed for row 2.
            Only cells that ALSO need HORIZONTAL pinning (Product/Metric)
            get their own extra `sticky left-*` — a cell can be sticky on
            one axis via its own rule while inheriting the other axis from
            an ancestor's sticky box. */}
        <thead className="sticky top-0 z-20 bg-neutral-50">
          <tr>
            <th
              rowSpan={2}
              className="sticky left-0 z-30 border-b border-r border-neutral-200 bg-neutral-50 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600"
              style={{ width: PRODUCT_COL_WIDTH, minWidth: PRODUCT_COL_WIDTH }}
            >
              Product
            </th>
            <th
              rowSpan={2}
              className="sticky z-30 border-b border-r border-neutral-200 bg-neutral-50 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600"
              style={{ left: PRODUCT_COL_WIDTH, width: 90, minWidth: 90 }}
            >
              Metric
            </th>
            {orderedMonths.map((month) => {
              const expanded = expandedMonths.has(month)
              return (
                <th
                  key={month}
                  colSpan={monthSpans.get(month) ?? 1}
                  className="border-b border-r-2 border-r-neutral-300 bg-neutral-50 p-0 text-center text-[11px] font-semibold text-neutral-600"
                >
                  <button
                    type="button"
                    onClick={() => toggleMonth(month)}
                    aria-expanded={expanded}
                    title={expanded ? 'Collapse this month' : 'Expand this month into weeks'}
                    className="flex h-9 w-full items-center justify-center gap-1 px-2 hover:bg-neutral-100 focus:outline-none focus:ring-1 focus:ring-primary-500"
                  >
                    {expanded ? <ChevronDown aria-hidden className="h-3 w-3 shrink-0" /> : <ChevronRight aria-hidden className="h-3 w-3 shrink-0" />}
                    {month}
                  </button>
                </th>
              )
            })}
          </tr>
          <tr>
            {columns.map((col) => {
              const isMaintenance = col.kind === 'week' && maintenanceWeeks.has(col.week_start)
              return (
                <th
                  key={col.id}
                  className={cn(
                    'h-7 border-b border-r border-neutral-200 p-0 text-center text-[10px] font-medium text-neutral-500',
                    isMaintenance ? 'bg-neutral-200' : 'bg-neutral-50',
                  )}
                  style={{ minWidth: WEEK_COL_MIN_WIDTH }}
                >
                  {col.kind === 'week' && onOpenWeekDrawer ? (
                    <button
                      type="button"
                      onClick={() => {
                        const week = weekGridByStart.get(col.week_start)
                        if (week) onOpenWeekDrawer(week)
                      }}
                      title={`${col.label ?? col.week_start}${isMaintenance ? ' — maintenance week (click to view/edit)' : ' — click to mark as a maintenance week'}`}
                      className="flex h-7 w-full min-w-[44px] items-center justify-center gap-1 px-1.5 hover:bg-primary-50 hover:text-primary-700 focus:outline-none focus:ring-1 focus:ring-primary-500"
                    >
                      {isMaintenance && <Wrench aria-hidden className="h-3 w-3 shrink-0 text-neutral-500" />}
                      {weekColumnShortLabel(col)}
                    </button>
                  ) : (
                    <span className="flex h-7 items-center justify-center px-1.5" title={col.kind === 'week' ? (col.label ?? col.week_start) : undefined}>
                      {col.kind === 'week' ? weekColumnShortLabel(col) : ''}
                    </span>
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {products.map((product) => (
            <ProductRows
              key={product.code}
              product={product}
              noBom={!!noBomCodes?.has(product.code)}
              columns={columns}
              orderedMonths={orderedMonths}
              monthSpans={monthSpans}
              getMonthCell={getMonthCell}
              getPlannedCell={getPlannedCell}
              maintenanceWeeks={maintenanceWeeks}
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

/** Short header text for a week column: the part of the server's
 *  `week_label` before ' · ' (e.g. 'Sep W4' or '2026-W40'), with the full
 *  label (including the day range) as the hover tooltip via the caller's
 *  `title`. `label` is always present in practice (`week_grid` computes one
 *  for every entry, including a week with zero lines) — the bare
 *  `week_start` fallback is defensive only, for a `WeekRef` some future
 *  caller constructs by hand without one. */
function weekColumnShortLabel(col: WeekColumn): string {
  if (col.label) return col.label.split(' · ')[0]
  return col.week_start
}

type MetricRow = 'Demand' | 'Available' | 'Planned'

// Demand/Available context values: a real 0 is rendered as muted as the "—"
// no-data dash so the (many) zero cells recede instead of shouting.
function MetricValue({
  value,
  hasData,
  formatValue,
}: {
  value: number
  hasData: boolean
  formatValue: (kg: number) => string
}) {
  if (!hasData) return <span className="text-neutral-300">—</span>
  if (value === 0) return <span className="text-neutral-300">{formatValue(0)}</span>
  return <>{formatValue(value)}</>
}

function ProductRows({
  product,
  noBom,
  columns,
  orderedMonths,
  monthSpans,
  getMonthCell,
  getPlannedCell,
  maintenanceWeeks,
  formatValue,
  onAdjustCell,
  readOnly,
}: {
  product: { code: string; name: string }
  noBom: boolean
  columns: Column[]
  orderedMonths: string[]
  monthSpans: Map<string, number>
  getMonthCell: (materialCode: string, month: string) => MatrixCell
  getPlannedCell: (materialCode: string, col: Column) => MatrixCell
  maintenanceWeeks: ReadonlySet<string>
  formatValue: (kg: number) => string
  onAdjustCell: (lines: MpsLine[]) => void
  readOnly: boolean
}) {
  const productCell = (
    <td
      rowSpan={3}
      className="sticky left-0 z-10 border-b border-r border-t-2 border-t-neutral-300 border-neutral-200 bg-white px-3 py-1.5 align-top text-left font-medium text-neutral-800"
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
  )

  function metricLabelCell(metric: MetricRow, borderTop: boolean) {
    return (
      <td
        className={cn(
          'sticky z-10 border-b border-r border-neutral-200 bg-neutral-50 px-3 py-1.5 text-left text-[11px] font-medium text-neutral-500',
          borderTop && 'border-t-2 border-t-neutral-300',
        )}
        style={{ left: PRODUCT_COL_WIDTH, width: 90, minWidth: 90 }}
      >
        {metric}
      </td>
    )
  }

  return (
    <>
      {/* Demand — monthly, spans + centres over that month's week columns
          (or its single collapsed summary column). */}
      <tr className="border-b border-neutral-100">
        {productCell}
        {metricLabelCell('Demand', true)}
        {orderedMonths.map((month) => (
          <MonthMetricCell
            key={month}
            metric="Demand"
            span={monthSpans.get(month) ?? 1}
            borderTop
            cell={getMonthCell(product.code, month)}
            formatValue={formatValue}
          />
        ))}
      </tr>

      {/* Available — monthly, same spanning rule as Demand. */}
      <tr className="border-b border-neutral-100">
        {metricLabelCell('Available', false)}
        {orderedMonths.map((month) => (
          <MonthMetricCell
            key={month}
            metric="Available"
            span={monthSpans.get(month) ?? 1}
            borderTop={false}
            cell={getMonthCell(product.code, month)}
            formatValue={formatValue}
          />
        ))}
      </tr>

      {/* Planned — the only per-week row: one cell per column, whether that
          column is a real week or a collapsed month's summary. */}
      <tr className="border-b-0">
        {metricLabelCell('Planned', false)}
        {columns.map((col) => (
          <PlannedCell
            key={col.id}
            cell={getPlannedCell(product.code, col)}
            isMaintenance={col.kind === 'week' && maintenanceWeeks.has(col.week_start)}
            formatValue={formatValue}
            onAdjustCell={onAdjustCell}
            readOnly={readOnly}
          />
        ))}
      </tr>
    </>
  )
}

function MonthMetricCell({
  metric,
  span,
  borderTop,
  cell,
  formatValue,
}: {
  metric: 'Demand' | 'Available'
  span: number
  borderTop: boolean
  cell: MatrixCell
  formatValue: (kg: number) => string
}) {
  const hasData = cell.cellLines.length > 0
  const value = metric === 'Demand' ? cell.demand : cell.available
  return (
    <td
      colSpan={span}
      className={cn(
        'h-10 border-b border-r border-neutral-100 bg-white px-2 text-center font-mono',
        metric === 'Demand' ? 'text-neutral-700' : 'text-neutral-500',
        borderTop && 'border-t-2 border-t-neutral-300',
      )}
    >
      <MetricValue value={value} hasData={hasData} formatValue={formatValue} />
    </td>
  )
}

function PlannedCell({
  cell,
  isMaintenance,
  formatValue,
  onAdjustCell,
  readOnly,
}: {
  cell: MatrixCell
  /** True for a week column an active capacity exception CLOSES —
   *  `max_output_qty <= 0` or `max_sku_count < 1`, see ProductionMatrix's
   *  `maintenanceWeeks` — tints the cell grey.
   *  Never true for a collapsed month's summary column (the caller only
   *  sets this for `col.kind === 'week'`). */
  isMaintenance: boolean
  formatValue: (kg: number) => string
  onAdjustCell: (lines: MpsLine[]) => void
  readOnly: boolean
}) {
  // "Has production" = a non-zero REAL planned qty (aggregateLines now
  // excludes capacity_gap lines' qty from `planned` — review round 1).
  // `showCell` widens that to "has anything worth showing": a cell that is
  // ENTIRELY a gap now correctly aggregates to `planned === 0`, but it must
  // still render as the red/alert cell, not silently degrade to the "—"
  // empty state below — that would regress "keep red gap cells" the moment
  // a gap has no real production alongside it in the same cell. A cell
  // with neither production nor a gap (e.g. fully covered by available
  // stock) is the only case that renders as empty, same as a cell with no
  // lines at all.
  const hasProduction = cell.planned > 0
  const showCell = hasProduction || cell.gap
  const title = cell.demandMonths.length > 0 ? `For ${cell.demandMonths.join(', ')} demand` : undefined
  // Gap (red, unmet demand) takes precedence over shortfall (amber,
  // produced later than the requested lead but still met) when a cell is
  // both — gap is the more severe condition a planner needs to see first.
  const showShortfall = cell.shortfall && !cell.gap
  // Sum of the GAP lines' own qty (aggregateLines deliberately excludes it
  // from `cell.planned`, see that function's own comment) — the cell
  // already renders red/bold on a gap, but until now that only told a
  // planner THAT demand went unmet, not by how much. Recovered straight
  // from `cellLines` (already handed to this cell for the click-to-adjust
  // affordance), so this needs no new data plumbing.
  const gapQty = cell.gap
    ? cell.cellLines.filter((l) => l.capacity_gap).reduce((sum, l) => sum + Number(l.qty), 0)
    : 0

  if (!showCell) {
    return (
      <td className={cn('h-10 border-b border-r border-neutral-100 px-2 text-right font-mono text-neutral-300', isMaintenance ? 'bg-neutral-200' : 'bg-success-50')}>—</td>
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
    ? `Capacity gap — ${formatValue(gapQty)} unmet demand${title ? ` (${title})` : ''}`
    : showShortfall
      ? 'Produced later than the lead — no earlier capacity/time'
      : title

  return (
    <td
      className={cn(
        'h-10 border-b border-r border-neutral-100 px-2 text-right font-mono text-sm font-bold',
        cell.gap
          ? 'bg-danger-50 text-danger-700'
          : showShortfall
            ? 'bg-warning-50 text-warning-800'
            : isMaintenance
              ? 'bg-neutral-200 text-neutral-700'
              : 'bg-success-50 text-neutral-800',
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
