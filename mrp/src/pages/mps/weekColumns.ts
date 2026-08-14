// weekColumns — pure column-model logic for the weekly Production Plan
// matrix (Production Plan / MPS page, Task 9, design §5.1). No React here:
// this module only decides WHICH columns exist, in what order, and whether
// a given month renders as N week columns or 1 collapsed summary column,
// given the run's lines and the set of months the planner has expanded.
//
// Verified by weekColumns.verify.ts (`npx tsx`) per this repo's
// no-frontend-test-framework convention — see
// components/matrixGrid/verify.ts's own header comment for precedent.
//
// ProductionMatrix.tsx is the only consumer: it builds a `WeekRef[]` from
// the run's `MpsLine[]` (grouping by `plan_week_start`/`plan_week_month`)
// plus one placeholder WeekRef per horizon month that has NO lines at all
// (see its `buildWeekRefs`), then calls `buildWeekColumns` with the
// planner's current expand/collapse state.

/**
 * One known week: either a real plan week carried by some line
 * (`plan_week_start`/`plan_week_month` off the wire, `week_label` as
 * `label`), or a synthetic placeholder the caller invents for a month with
 * zero lines — this module never inspects the date itself beyond sorting
 * and grouping by it, so a placeholder's exact day-of-month is cosmetic.
 */
export interface WeekRef {
  /** ISO date, e.g. '2026-08-03'. */
  week_start: string
  /** 'YYYY-MM', the week's owning month. */
  month: string
  /** Pre-rendered label for a real week (mrp-api's `week_label`, e.g.
   *  'Sep W4 · Sep 22–28' or '2026-W40 · Sep 28–Oct 4'). Omitted for a
   *  placeholder — there is no real week to label. */
  label?: string
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
 * still gets a column" is the CALLER's responsibility — it must include at
 * least one `WeekRef` for every month it wants represented on the axis,
 * including a placeholder for a month with zero plan lines. See
 * ProductionMatrix.tsx's `buildWeekRefs`.
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
 * 'YYYY-MM' + `months` steps, wrapping years — pure calendar arithmetic
 * (calendar months, NOT the run's ISO/fixed week grid). Used to enumerate a
 * run's declared horizon (`horizon_start_month` + `horizon_months`)
 * independent of which months happen to carry lines, so a month whose net
 * demand nets to zero still contributes a placeholder column instead of
 * silently vanishing from the time axis.
 */
export function monthsInHorizon(startMonth: string, months: number): string[] {
  const [y, m] = startMonth.split('-').map(Number)
  const out: string[] = []
  for (let i = 0; i < months; i++) {
    const total = m - 1 + i
    const yy = y + Math.floor(total / 12)
    const mm = (total % 12) + 1
    out.push(`${yy}-${String(mm).padStart(2, '0')}`)
  }
  return out
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
