// weekColumns — pure column-model logic for the weekly Production Plan
// matrix (Production Plan / MPS page, Task 9, design §5.1). No React here:
// this module only decides WHICH columns exist, in what order, and whether
// a given month renders as N week columns or 1 collapsed summary column,
// given the run's own week grid and the set of months the planner has
// expanded.
//
// Verified by weekColumns.verify.ts (`npx tsx`) per this repo's
// no-frontend-test-framework convention — see
// components/matrixGrid/verify.ts's own header comment for precedent.
//
// ProductionMatrix.tsx is the only consumer: it converts the run's
// backend-computed `week_grid` (`GET /runs/{id}`'s `week_grid`, mrp-api's
// `_compute_week_grid`) into `WeekRef[]` via `buildWeekRefs`, then calls
// `buildWeekColumns` with the planner's current expand/collapse state.
//
// ## Fix-round-1 history (do not reintroduce)
//
// The first version of this module had the CALLER (ProductionMatrix.tsx)
// derive "which weeks exist" from `lines` plus the run's declared horizon,
// synthesizing a placeholder week for any month with zero lines. Review
// caught two real defects in that approach: (a) a maintenance week has
// ZERO lines by construction (that is what "no production this week"
// means), so it got no column and no header for the WeekDrawer to attach
// to; (b) an expanded but genuinely empty month rendered exactly ONE fake
// week column labelled with the synthetic placeholder's made-up date
// (e.g. `2026-10-01`), which reads as a real week to a planner. Both are
// dissolved by switching to the backend's `week_grid`, which already knows
// every real week of every horizon month — including ones with zero
// lines — because it is computed the same way `mps_export.py`'s own
// `week_grid` is (see `_compute_week_grid`'s docstring in
// `app/api/v1/mps.py`). `buildWeekRefs` below is now a pure pass-through of
// that list; there is no more synthesis, and `monthsInHorizon` (which only
// ever existed to support that synthesis) has been removed.

/**
 * One known week, sourced from the run's `week_grid` (`GET /runs/{id}`,
 * mrp-api's `_compute_week_grid`) — the authoritative "which weeks exist"
 * list. Never derive this from `lines`: a maintenance week or a
 * zero-net-demand month has no lines by construction and would silently
 * lose its column.
 */
export interface WeekRef {
  /** ISO date, e.g. '2026-08-03'. */
  week_start: string
  /** 'YYYY-MM', the week's owning month. */
  month: string
  /** Rendered label from the run's OWN `week_calendar_mode` (mrp-api's
   *  `week_label`, e.g. 'Sep W4 · Sep 22–28' or '2026-W40 · Sep 28–Oct 4') —
   *  always present in practice (`week_grid` computes one for every entry),
   *  optional here only so `buildWeekColumns`/`Column` don't have to assume
   *  a caller never constructs a `WeekRef` by hand (e.g. in a test) without
   *  one. */
  label?: string
}

/** The shape `GET /runs/{id}`'s `week_grid` entries arrive in on the wire
 *  (see mrp-api's `WeekGridEntry` / the frontend's `mpsApi.ts` `WeekGridEntry`
 *  type) — structurally duck-typed here rather than importing `mpsApi.ts`'s
 *  type, so this module stays free of any wire-format dependency (same
 *  discipline `buildWeekColumns` itself already follows). */
export interface WeekGridEntryLike {
  week_start: string
  week_month: string
  label: string
}

/**
 * Converts the run's backend-computed week grid into the `WeekRef[]` shape
 * `buildWeekColumns` consumes — a faithful, order-preserving, non-filtering
 * map (`week_month` -> `month`, `label` carried through unchanged). This is
 * intentionally almost too simple to need a function: it exists so that
 * (a) the "which weeks exist" decision has exactly ONE call site to read in
 * `ProductionMatrix.tsx` instead of being buried in an inline `useMemo`
 * (that opacity was itself an earlier review finding — the empty-month
 * guarantee lived somewhere `weekColumns.verify.ts` could not reach it),
 * and (b) it is a place to pin, by test, that nothing here ever drops or
 * re-derives a week_grid entry.
 */
export function buildWeekRefs(weekGrid: WeekGridEntryLike[]): WeekRef[] {
  return weekGrid.map((g) => ({ week_start: g.week_start, month: g.week_month, label: g.label }))
}

export interface WeekColumn {
  kind: 'week'
  id: string
  month: string
  week_start: string
  label?: string
}

export interface MonthSummaryColumn {
  kind: 'monthSummary'
  id: string
  month: string
}

export type Column = WeekColumn | MonthSummaryColumn

/**
 * Groups `weeks` by month (de-duplicating repeated `week_start` values
 * within a month — several lines commonly share a week) and, per month in
 * ascending order, emits either one `week` Column per distinct week
 * (expanded) or exactly one `monthSummary` Column (collapsed).
 *
 * A month with NO entries anywhere in `weeks` produces NO column: this
 * function only groups what it is given. Guaranteeing "an empty month
 * still gets a column" is now entirely `week_grid`'s job (mrp-api's
 * `_compute_week_grid` walks the run's full declared horizon regardless of
 * which months have lines) plus `buildWeekRefs` faithfully passing every
 * entry through — this function's only remaining contract is to never drop
 * a month it IS given.
 */
export function buildWeekColumns(weeks: WeekRef[], expandedMonths: ReadonlySet<string>): Column[] {
  const byMonth = new Map<string, WeekRef[]>()
  for (const w of weeks) {
    let list = byMonth.get(w.month)
    if (!list) {
      list = []
      byMonth.set(w.month, list)
    }
    if (!list.some((x) => x.week_start === w.week_start)) list.push(w)
  }

  const months = [...byMonth.keys()].sort()
  const columns: Column[] = []
  for (const month of months) {
    const weeksInMonth = [...(byMonth.get(month) ?? [])].sort((a, b) => a.week_start.localeCompare(b.week_start))
    if (expandedMonths.has(month)) {
      for (const w of weeksInMonth) {
        columns.push({ kind: 'week', id: `w:${w.week_start}`, month, week_start: w.week_start, label: w.label })
      }
    } else {
      columns.push({ kind: 'monthSummary', id: `m:${month}`, month })
    }
  }
  return columns
}

/**
 * Default expand set per design §5.1: "current month + next 2", so the
 * opening view is ~13 columns rather than a full ~78-week horizon.
 * `months` must already be sorted ascending. If today's calendar month is
 * not in `months` at all (viewing a run whose horizon has fully elapsed, or
 * one that starts in the future) this falls back to the horizon's first 3
 * months, so the opening view is never fully collapsed.
 */
export function defaultExpandedMonths(months: string[], today: Date = new Date()): Set<string> {
  const current = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
  const idx = months.indexOf(current)
  const start = idx >= 0 ? idx : 0
  return new Set(months.slice(start, start + 3))
}
