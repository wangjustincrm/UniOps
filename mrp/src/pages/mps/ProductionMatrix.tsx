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
// comment below for why. The pinned weekly-total row (design §5.1's last
// bullet) mirrors this at the OTHER end of the table: `<tfoot sticky
// bottom-0>` is its own single sticky unit, with the same "corner cells get
// an extra `sticky left-*`" treatment as the header — see the `<tfoot>`
// element's own comment. The totalling itself (which products' Planned
// cells feed which column) is pure logic in `weekColumns.ts`'s
// `sumPlannedByColumn`, verified by `weekColumns.verify.ts` — this file only
// builds the per-(product, column) contributions and renders the result.
import { useMemo, useState } from 'react'
import { Lock, AlertTriangle, Clock, ChevronDown, ChevronRight, Wrench } from 'lucide-react'
import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { MaterialOption } from '@/lib/materials'
import type { MpsLine, WeekGridEntry } from './mpsApi'
import {
  buildWeekColumns, buildWeekRefs, defaultExpandedMonths, sumPlannedByColumn,
  type Column, type PlannedContribution, type WeekColumn,
} from './weekColumns'

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
  /** Minimum lot size (mrp11). `carriedIn` is the part of `available` that
   *  came from an earlier batch's surplus rather than from stock on hand —
   *  shown in the tooltip so "Available 15 t" is never a number with no
   *  provenance. `surplus` is how much of `planned` exceeds the net
   *  requirement because the batch was rounded up to a whole lot.
   *  `covered` marks a month whose whole requirement was met by that
   *  surplus, which produces a qty-0 line: without it the cell would render
   *  as an ordinary empty one and read as "no demand" instead of
   *  "already made". */
  carriedIn: number
  surplus: number
  covered: boolean
  late: boolean
  expiryRisk: boolean
  belowMinLot: boolean
  demandMonths: string[]
  /** The underlying MpsLine[] this cell aggregates — handed back verbatim
   *  to onAdjustCell so the caller (AdjustDrawer) knows exactly which
   *  lines a click on this cell refers to. */
  cellLines: MpsLine[]
}

function emptyCell(): MatrixCell {
  return {
    demand: 0, available: 0, planned: 0, gap: false, locked: false, shortfall: false,
    carriedIn: 0, surplus: 0, covered: false, late: false, expiryRisk: false,
    belowMinLot: false, demandMonths: [], cellLines: [],
  }
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
    // Per LINE, not per demand month: a levelled run is several lines and
    // each carries its own share of the rounded-up quantity.
    cell.surplus += Number(line.surplus_qty)
    if (line.covered_by_carry) cell.covered = true
    if (line.late_production) cell.late = true
    if (line.surplus_expiry_risk) cell.expiryRisk = true
    if (line.below_min_lot) cell.belowMinLot = true
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
      // Stock on hand PLUS whatever an earlier batch's surplus left for
      // this month. Without the carry the matrix shows "Demand 5,
      // Available 0, Planned 0" for a month that is fully covered — three
      // numbers that contradict each other.
      cell.available += Number(line.opening_stock) + Number(line.carry_in_qty)
      cell.carriedIn += Number(line.carry_in_qty)
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
  /** Last month of the run's frozen zone ('YYYY-MM'), or null when nothing
   *  is frozen. Those columns are greyed and their cells are not clickable:
   *  the materials are bought, and the API refuses to change them anyway. */
  frozenUntilMonth?: string | null
  /** Per-cell changes against another version, keyed
   *  `<material_code>::<plan_week_start>` (`weekCellKey`). Undefined when no
   *  comparison is on. Cells that exist ONLY in the baseline are included:
   *  a product moved out of a week has no line here, and dropping those
   *  would hide half of what changed. */
  diffByCell?: Map<string, { before: number; after: number; delta: number }>
}

export function ProductionMatrix({
  lines,
  frozenUntilMonth = null,
  diffByCell,
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
    // Union with whatever the comparison mentions: a product that this
    // version no longer makes has NO line here, so deriving the rows from
    // `lines` alone would silently drop "this product was moved out
    // entirely" — the change a planner most needs to see.
    const codes = new Set(lines.map((l) => l.material_code))
    if (diffByCell) {
      for (const key of diffByCell.keys()) codes.add(key.split('::')[0])
    }
    return [...codes].sort()
      .map((code) => ({ code, name: materialsByCode.get(code)?.name ?? code }))
  }, [lines, materialsByCode, diffByCell])

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

  /** Months that contain at least one changed cell, with the count.
   *
   *  Months start COLLAPSED, so without this a planner has to expand all 18
   *  of them one at a time to find out where anything moved -- which is the
   *  work the comparison exists to remove. The month header says it before
   *  anything is expanded.
   *
   *  Keyed off the run's own week grid rather than parsing the week-start
   *  date, so a week that belongs to a neighbouring month under the ISO
   *  rules is counted in the month the plan actually files it under. */
  const diffCountByMonth = useMemo(() => {
    const counts = new Map<string, number>()
    if (!diffByCell) return counts
    for (const key of diffByCell.keys()) {
      const weekStart = key.slice(key.indexOf('::') + 2)
      // The grid is the authority on which month owns a week (ISO weeks can
      // belong to a neighbouring month). A change whose week is not in THIS
      // run's grid — the baseline planned something in a week this plan has
      // no column for — falls back to the date's own month rather than being
      // dropped: a change nobody can see is worse than one filed a month off.
      // The summary bar still carries the authoritative totals from the API.
      const month = weekGridByStart.get(weekStart)?.week_month ?? weekStart.slice(0, 7)
      counts.set(month, (counts.get(month) ?? 0) + 1)
    }
    return counts
  }, [diffByCell, weekGridByStart])

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

  // Readability fix (business owner: "边框颜色太淡了，很难轻松区分周" — the
  // Planned row's week-to-week separators were nearly invisible under its
  // green tint). The header already distinguishes a plain week separator
  // (`border-neutral-200`) from a month boundary (`border-r-2
  // border-r-neutral-300`, on the month `<th>`'s own right edge) — this set
  // carries that SAME distinction down into the body/footer, so a planner
  // scrolling horizontally can tell "next week" from "next month" at a
  // glance, not just in the header row. A column is a boundary when it is
  // the LAST column belonging to its month in the flat `columns` list
  // (works identically for a real week column or a collapsed month's single
  // summary column — either way, that is where the month visually ends).
  const monthBoundaryColumnIds = useMemo(() => {
    const ids = new Set<string>()
    for (let i = 0; i < columns.length; i++) {
      const isLast = i === columns.length - 1 || columns[i + 1].month !== columns[i].month
      if (isLast) ids.add(columns[i].id)
    }
    return ids
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

  // Pinned weekly-total footer row (design §5.1's last bullet) — one
  // contribution per (product, column), reading the SAME `getPlannedCell`
  // the Planned row itself renders from, so the footer can never disagree
  // with the column of numbers it sits under (a collapsed month's summary
  // column and an expanded month's per-week columns each already resolve to
  // the right grain via `getPlannedCell` — see that function's own doc).
  // `.planned` is capacity_gap-EXCLUDED by `aggregateLines` already (see its
  // header comment), so this sums real output only, matching the spec's
  // "只统计实产、排除 capacity_gap 行的量".
  const weekTotals = useMemo(() => {
    const contributions: PlannedContribution[] = []
    for (const product of products) {
      for (const col of columns) {
        contributions.push({ columnId: col.id, planned: getPlannedCell(product.code, col).planned })
      }
    }
    return sumPlannedByColumn(contributions, columns)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- getPlannedCell closes over monthCellMap/weekCellMap, both already in this memo's real dependency chain via `lines`
  }, [products, columns, monthCellMap, weekCellMap])

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
              const changed = diffCountByMonth.get(month) ?? 0
              return (
                <th
                  key={month}
                  colSpan={monthSpans.get(month) ?? 1}
                  className={cn(
                    'border-b border-r-2 border-r-neutral-300 p-0 text-center text-[11px] font-semibold',
                    // A changed month is called out on the header itself, so
                    // "where did anything move" is answerable with every
                    // month still collapsed.
                    changed > 0
                      ? 'bg-primary-100 text-primary-900'
                      : 'bg-neutral-50 text-neutral-600',
                  )}
                >
                  <button
                    type="button"
                    onClick={() => toggleMonth(month)}
                    aria-expanded={expanded}
                    title={changed > 0
                      ? `${changed} change(s) against the compared version — `
                        + (expanded ? 'collapse this month' : 'expand to see which weeks')
                      : expanded ? 'Collapse this month' : 'Expand this month into weeks'}
                    className={cn(
                      'flex h-9 w-full items-center justify-center gap-1 px-2 focus:outline-none focus:ring-1 focus:ring-primary-500',
                      changed > 0 ? 'hover:bg-primary-200' : 'hover:bg-neutral-100',
                    )}
                  >
                    {expanded ? <ChevronDown aria-hidden className="h-3 w-3 shrink-0" /> : <ChevronRight aria-hidden className="h-3 w-3 shrink-0" />}
                    {month}
                    {changed > 0 && (
                      // The count, not just a dot: "12 cells moved" and "one
                      // cell moved" are different situations and a planner
                      // decides which month to open on exactly that.
                      <span
                        aria-label={`${changed} changed cell(s)`}
                        className="rounded-full bg-primary-600 px-1.5 text-[10px] font-bold leading-4 text-white"
                      >
                        {changed}
                      </span>
                    )}
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
              monthBoundaryColumnIds={monthBoundaryColumnIds}
              formatValue={formatValue}
              onAdjustCell={onAdjustCell}
              readOnly={readOnly}
              frozenUntilMonth={frozenUntilMonth}
              diffByCell={diffByCell}
            />
          ))}
        </tbody>
        {/* Pinned weekly-total footer (design §5.1's last bullet, "★底部锁死
            的周合计行") — mirrors the header's own sticky technique exactly:
            the WHOLE `<tfoot>` is the sticky-bottom unit (`sticky bottom-0`),
            not per-cell offsets, same reasoning as the `<thead>` comment
            above. Its two left-hand cells get their OWN extra `sticky
            left-*` for horizontal pinning, at the SAME z-tier as the
            header's corner cells (z-30) — the header corner and the footer's
            left cells never occupy the same pixels at once (one pins top,
            the other bottom), so sharing a tier is safe and keeps this to
            the two z-tiers the header already established (z-20 for the
            sticky unit itself, z-30 for the bit that's ALSO pinned
            horizontally) rather than inventing a third. */}
        <tfoot className="sticky bottom-0 z-20 bg-neutral-50">
          <tr>
            <td
              className="sticky left-0 z-30 border-t-2 border-r border-t-neutral-300 border-neutral-200 bg-neutral-50 px-3 py-1.5 text-left text-[11px] font-semibold text-neutral-600"
              style={{ width: PRODUCT_COL_WIDTH, minWidth: PRODUCT_COL_WIDTH }}
            />
            <td
              className="sticky z-30 border-t-2 border-r border-t-neutral-300 border-neutral-200 bg-neutral-50 px-3 py-1.5 text-left text-[11px] font-semibold text-neutral-600"
              style={{ left: PRODUCT_COL_WIDTH, width: 90, minWidth: 90 }}
            >
              Total
            </td>
            {columns.map((col) => {
              const total = weekTotals.get(col.id) ?? 0
              const isMonthBoundary = monthBoundaryColumnIds.has(col.id)
              return (
                <td
                  key={col.id}
                  className={cn(
                    'h-8 border-t-2 border-t-neutral-300 bg-neutral-50 px-2 text-right font-mono text-xs font-semibold text-neutral-700',
                    isMonthBoundary ? 'border-r-2 border-r-neutral-300' : 'border-r border-r-neutral-200',
                  )}
                >
                  {total === 0 ? <span className="text-neutral-300">—</span> : formatValue(total)}
                </td>
              )
            })}
          </tr>
        </tfoot>
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
  monthBoundaryColumnIds,
  formatValue,
  onAdjustCell,
  readOnly,
  frozenUntilMonth,
  diffByCell,
}: {
  product: { code: string; name: string }
  noBom: boolean
  columns: Column[]
  orderedMonths: string[]
  monthSpans: Map<string, number>
  getMonthCell: (materialCode: string, month: string) => MatrixCell
  getPlannedCell: (materialCode: string, col: Column) => MatrixCell
  maintenanceWeeks: ReadonlySet<string>
  /** Column ids that sit at a month's right edge (readability fix, see
   *  ProductionMatrix's own `monthBoundaryColumnIds` doc) — strengthens the
   *  Planned row's separator there to match the header's month boundary. */
  monthBoundaryColumnIds: ReadonlySet<string>
  formatValue: (kg: number) => string
  onAdjustCell: (lines: MpsLine[]) => void
  readOnly: boolean
  /** Last month of the run's frozen zone, or null — those columns are
   *  greyed and never clickable. */
  frozenUntilMonth: string | null
  diffByCell?: Map<string, { before: number; after: number; delta: number }>
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
            isMonthBoundary={monthBoundaryColumnIds.has(col.id)}
            formatValue={formatValue}
            onAdjustCell={onAdjustCell}
            // A frozen month is read-only whatever the run's own status: its
            // materials are bought, and the API refuses the PATCH anyway —
            // a clickable cell that always errors is worse than no click.
            readOnly={readOnly || (!!frozenUntilMonth && col.month <= frozenUntilMonth)}
            isFrozen={!!frozenUntilMonth && col.month <= frozenUntilMonth}
            // Only week columns carry a comparison: a collapsed month's
            // summary column aggregates several weeks, and a single arrow
            // on it would say "something in here moved" without saying what.
            diff={col.kind === 'week'
              ? diffByCell?.get(weekCellKey(product.code, col.week_start))
              : undefined}
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
  const title = metric === 'Available' && cell.carriedIn > 0
    ? `Includes ${formatValue(cell.carriedIn)} carried from an earlier minimum-lot batch`
    : undefined
  return (
    <td
      title={title}
      colSpan={span}
      className={cn(
        // This cell's colSpan always covers the WHOLE month (span =
        // monthSpans.get(month), same count whether the month is collapsed
        // to one column or expanded to its weeks) — its right edge is
        // therefore always a month boundary, never an interior week
        // separator, so it always gets the header's stronger boundary
        // treatment (border-r-2 border-r-neutral-300), never the plain
        // border-neutral-100 a same-row interior line would use if one
        // existed here.
        'h-10 border-b border-b-neutral-100 border-r-2 border-r-neutral-300 bg-white px-2 text-center font-mono',
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
  isMonthBoundary,
  formatValue,
  onAdjustCell,
  readOnly,
  isFrozen,
  diff,
}: {
  cell: MatrixCell
  /** True for a week column an active capacity exception CLOSES —
   *  `max_output_qty <= 0` or `max_sku_count < 1`, see ProductionMatrix's
   *  `maintenanceWeeks` — tints the cell grey.
   *  Never true for a collapsed month's summary column (the caller only
   *  sets this for `col.kind === 'week'`). */
  isMaintenance: boolean
  /** True when this column is the last one belonging to its month
   *  (ProductionMatrix's `monthBoundaryColumnIds`) — strengthens the right
   *  border to match the header's own month-boundary line, so a planner
   *  scrolling the (only per-week-granular) Planned row can tell "next
   *  week" from "next month" the same way the header already can. */
  isMonthBoundary: boolean
  formatValue: (kg: number) => string
  onAdjustCell: (lines: MpsLine[]) => void
  readOnly: boolean
  /** Inside the run's frozen zone — tinted like a maintenance week and
   *  never clickable. */
  isFrozen?: boolean
  /** This cell's change against the compared version, when a comparison is
   *  on and this cell actually moved. */
  diff?: { before: number; after: number; delta: number }
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
  // A covered month has a real line (qty 0) and belongs on screen: it is
  // demand that was already made, not demand that does not exist.
  // A cell that exists only in the COMPARED version has no production here
  // at all; it still has to render, because "this week is empty now and was
  // not before" is the change worth seeing.
  const showCell = hasProduction || cell.gap || cell.covered || !!diff
  const title = cell.demandMonths.length > 0 ? `For ${cell.demandMonths.join(', ')} demand` : undefined
  // Gap (red, unmet demand) takes precedence over shortfall (amber,
  // produced later than the requested lead but still met) when a cell is
  // both — gap is the more severe condition a planner needs to see first.
  const showShortfall = cell.shortfall && !cell.gap
  // Produced AFTER the month that needed it, because neither that month nor
  // any earlier week could host a whole lot. Amber like the lead shortfall,
  // and outranked by a gap for the same reason.
  const showLate = cell.late && !cell.gap && !cell.shortfall
  // Sum of the GAP lines' own qty (aggregateLines deliberately excludes it
  // from `cell.planned`, see that function's own comment) — the cell
  // already renders red/bold on a gap, but until now that only told a
  // planner THAT demand went unmet, not by how much. Recovered straight
  // from `cellLines` (already handed to this cell for the click-to-adjust
  // affordance), so this needs no new data plumbing.
  const gapQty = cell.gap
    ? cell.cellLines.filter((l) => l.capacity_gap).reduce((sum, l) => sum + Number(l.qty), 0)
    : 0

  // Same two-tier right border the header uses (plain week separator vs.
  // a stronger month boundary) — shared by both this cell's empty and
  // populated render below so the grid line is identical regardless of
  // which branch renders, and by the footer/MonthMetricCell so the whole
  // column lines up top to bottom.
  const borderRClass = isMonthBoundary ? 'border-r-2 border-r-neutral-300' : 'border-r border-r-neutral-200'

  if (!showCell) {
    return (
      <td
        title={isFrozen ? 'Frozen — materials for this month are already purchased' : undefined}
        className={cn('h-10 border-b border-b-neutral-100 px-2 text-right font-mono text-neutral-300', borderRClass, isMaintenance || isFrozen ? 'bg-neutral-200' : 'bg-success-50')}
      >—</td>
    )
  }

  if (!hasProduction && !cell.gap && !cell.covered && diff) {
    return (
      <td
        title={`Was ${formatValue(diff.before)} in the compared version — nothing is planned here now`}
        className={cn(
          'h-10 border-b border-b-neutral-100 px-2 text-right font-mono text-sm text-danger-500 line-through',
          borderRClass, isMaintenance || isFrozen ? 'bg-neutral-200' : 'bg-success-50',
        )}
      >
        {formatValue(diff.before)}
      </td>
    )
  }

  if (!hasProduction && !cell.gap && cell.covered) {
    return (
      <td
        title={`Covered by an earlier minimum-lot batch${title ? ` (${title})` : ''}`}
        className={cn(
          'h-10 border-b border-b-neutral-100 px-2 text-right font-mono text-xs italic text-neutral-400',
          borderRClass, isMaintenance || isFrozen ? 'bg-neutral-200' : 'bg-success-50',
        )}
      >
        covered
      </td>
    )
  }

  const valueNode = (
    <span className="inline-flex items-center justify-end gap-1">
      {cell.gap && <AlertTriangle aria-hidden className="h-3 w-3 shrink-0 text-danger-600" />}
      {showShortfall && <Clock aria-hidden className="h-3 w-3 shrink-0 text-warning-600" />}
      {showLate && <Clock aria-hidden className="h-3 w-3 shrink-0 text-warning-600" />}
      {formatValue(cell.planned)}
      {cell.locked && <Lock aria-hidden className="h-3 w-3 shrink-0 text-neutral-400" />}
      {diff && (
        <span
          aria-hidden
          className={cn('shrink-0 text-[10px] font-bold',
            diff.delta > 0 ? 'text-success-600' : 'text-danger-600')}
        >
          {diff.delta > 0 ? '▲' : '▼'}
        </span>
      )}
    </span>
  )

  // Reads bottom-up: whatever else is true, a planner wants to know how
  // much of this number is not actually demanded.
  const lotNote = [
    cell.surplus > 0
      ? `${formatValue(cell.planned - cell.surplus)} required + ${formatValue(cell.surplus)} minimum-lot surplus`
      : null,
    cell.expiryRisk ? 'surplus may expire before it is used' : null,
    cell.belowMinLot ? 'below the minimum lot size — weekly capacity cannot reach it' : null,
  ].filter(Boolean).join('; ')

  const baseTitle = cell.gap
    ? `Capacity gap — ${formatValue(gapQty)} unmet demand${title ? ` (${title})` : ''}`
    : showShortfall
      ? 'Produced later than the lead — no earlier capacity/time'
      : showLate
        ? 'Produced after the month that needed it — nothing earlier could hold a whole lot'
        : title
  const diffNote = diff
    ? `Was ${formatValue(diff.before)} → now ${formatValue(diff.after)} `
      + `(${diff.delta > 0 ? '+' : ''}${formatValue(diff.delta)})`
    : null
  const cellTitle = [baseTitle, lotNote, diffNote].filter(Boolean).join(' · ') || undefined

  return (
    <td
      className={cn(
        'h-10 border-b border-b-neutral-100 px-2 text-right font-mono text-sm font-bold',
        borderRClass,
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
