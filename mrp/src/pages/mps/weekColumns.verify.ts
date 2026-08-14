/**
 * One-off verification script for weekColumns.ts's pure column-model logic
 * (week_grid -> WeekRef mapping / month grouping / collapse / expand /
 * default expand set). mrp has no test framework — see
 * components/matrixGrid/verify.ts's header comment for the established
 * plain-assert-script convention this follows.
 *
 * `check` takes a THUNK (`() => boolean`), not a pre-evaluated boolean, and
 * wraps its call in try/catch — fix-round-1 minor finding: the original
 * version evaluated each condition as an IIFE passed directly as an
 * argument, so a thrown error inside one check propagated straight out of
 * the whole script, aborting it before any later check (or the final
 * pass/fail summary) ever ran. The exit code still failed in that case, but
 * the printed report was misleading (silently truncated, not "N failed").
 *
 * Run with:  npx tsx src/pages/mps/weekColumns.verify.ts   (from mrp/)
 */
import { buildWeekColumns, buildWeekRefs, defaultExpandedMonths, type WeekGridEntryLike } from './weekColumns'

let failures = 0
function check(name: string, fn: () => boolean) {
  let cond: boolean
  try {
    cond = fn()
  } catch (err) {
    failures++
    console.log(`FAIL  ${name}  (threw: ${err instanceof Error ? err.message : String(err)})`)
    return
  }
  if (cond) console.log(`  ok  ${name}`)
  else { failures++; console.log(`FAIL  ${name}`) }
}

// ── buildWeekColumns ───────────────────────────────────────────────────────
console.log('buildWeekColumns')

const WEEKS = [
  { week_start: '2026-08-03', month: '2026-08' },
  { week_start: '2026-08-10', month: '2026-08' },
  { week_start: '2026-08-17', month: '2026-08' },
  { week_start: '2026-08-24', month: '2026-08' },
  { week_start: '2026-09-07', month: '2026-09' },
]

check('an expanded month contributes one column per week', () => {
  const cols = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09']))
  return cols.filter((c) => c.month === '2026-08' && c.kind === 'week').length === 4
})

check('a collapsed month contributes exactly one summary column', () => {
  const cols = buildWeekColumns(WEEKS, new Set(['2026-09']))
  const aug = cols.filter((c) => c.month === '2026-08')
  return aug.length === 1 && aug[0].kind === 'monthSummary'
})

// Fix-round-1: this used to read "a month with no planned week still
// occupies a column" and hand-construct a WeekRef for October directly —
// which only proved buildWeekColumns doesn't drop a month it's GIVEN, not
// that an empty month survives the real pipeline (review: "does not test
// that"). The real guarantee now lives one layer down, in buildWeekRefs
// (see that section below) — this check narrows to what buildWeekColumns
// itself is actually responsible for.
check('buildWeekColumns never drops a month present in its input, expanded or collapsed', () => {
  const withOct = [...WEEKS, { week_start: '2026-10-05', month: '2026-10' }]
  const collapsed = buildWeekColumns(withOct, new Set())
  const expanded = buildWeekColumns(withOct, new Set(['2026-10']))
  return collapsed.some((c) => c.month === '2026-10') && expanded.some((c) => c.month === '2026-10')
})

// Fix-round-1: replaces the old "collapsing then expanding returns the
// original column set" check. Review proved that one could not fail —
// `buildWeekColumns` is pure and stateless, so two calls with IDENTICAL
// arguments comparing equal is a property of the language, not of this
// module (confirmed: mutating the function to `return []` unconditionally
// still passed it). These two replacements test properties that a real bug
// actually could violate.
check('column ids are unique, and stable across repeated calls with identical input', () => {
  const a = buildWeekColumns(WEEKS, new Set(['2026-08']))
  const b = buildWeekColumns(WEEKS, new Set(['2026-08']))
  const idsA = a.map((c) => c.id)
  const idsB = b.map((c) => c.id)
  const unique = new Set(idsA).size === idsA.length
  const stableAcrossCalls = JSON.stringify(idsA) === JSON.stringify(idsB)
  return unique && stableAcrossCalls
})

check('expanding one month does not change another month\'s own columns (no cross-month coupling)', () => {
  const augCollapsedOnly = buildWeekColumns(WEEKS, new Set(['2026-09'])).filter((c) => c.month === '2026-09')
  const augAlsoExpanded = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09'])).filter((c) => c.month === '2026-09')
  return JSON.stringify(augCollapsedOnly) === JSON.stringify(augAlsoExpanded)
})

// ── buildWeekColumns: additional coverage ───────────────────────────────────
console.log('')
console.log('buildWeekColumns (additional)')

check('months come out in ascending chronological order regardless of input order', () => {
  const shuffled = [
    { week_start: '2026-09-07', month: '2026-09' },
    { week_start: '2026-07-06', month: '2026-07' },
    { week_start: '2026-08-03', month: '2026-08' },
  ]
  const cols = buildWeekColumns(shuffled, new Set(['2026-07', '2026-08', '2026-09']))
  return cols.map((c) => c.month).join() === ['2026-07', '2026-08', '2026-09'].join()
})

check('duplicate week_start within a month (two lines sharing a week) collapses to ONE week column', () => {
  const dup = [
    { week_start: '2026-08-03', month: '2026-08', label: 'Aug W1' },
    { week_start: '2026-08-03', month: '2026-08', label: 'Aug W1' },
  ]
  const cols = buildWeekColumns(dup, new Set(['2026-08']))
  return cols.length === 1 && cols[0].kind === 'week'
})

check('week columns within an expanded month are sorted chronologically', () => {
  const outOfOrder = [
    { week_start: '2026-08-24', month: '2026-08' },
    { week_start: '2026-08-03', month: '2026-08' },
    { week_start: '2026-08-10', month: '2026-08' },
  ]
  const cols = buildWeekColumns(outOfOrder, new Set(['2026-08']))
  return cols.map((c) => (c.kind === 'week' ? c.week_start : '')).join() ===
    ['2026-08-03', '2026-08-10', '2026-08-24'].join()
})

check('a real week_label survives onto its week Column', () => {
  const cols = buildWeekColumns(
    [{ week_start: '2026-08-03', month: '2026-08', label: 'Aug W1 · Aug 3–9' }],
    new Set(['2026-08']),
  )
  return cols[0].kind === 'week' && cols[0].label === 'Aug W1 · Aug 3–9'
})

check('empty input -> empty output, no throw', () => buildWeekColumns([], new Set()).length === 0)

// ── buildWeekRefs (fix-round-1: the empty-month guarantee's real home) ─────
console.log('')
console.log('buildWeekRefs')

const GRID: WeekGridEntryLike[] = [
  { week_start: '2026-08-03', week_month: '2026-08', label: 'Aug W1 · Aug 3–9' },
  { week_start: '2026-08-10', week_month: '2026-08', label: 'Aug W2 · Aug 10–16' },
  // 2026-09 stands in for a month with zero plan lines (a maintenance week,
  // or a demand month that netted to zero) — mrp-api's week_grid still
  // enumerates its real weeks (see _compute_week_grid's docstring), so
  // buildWeekRefs must carry all of them through untouched.
  { week_start: '2026-09-07', week_month: '2026-09', label: 'Sep W1 · Sep 7–13' },
  { week_start: '2026-09-14', week_month: '2026-09', label: 'Sep W2 · Sep 14–20' },
]

check('buildWeekRefs preserves every week_grid entry, in order, field for field', () => {
  const refs = buildWeekRefs(GRID)
  return refs.length === GRID.length && refs.every((r, i) =>
    r.week_start === GRID[i].week_start && r.month === GRID[i].week_month && r.label === GRID[i].label)
})

check('a month with no lines of its own (only present because week_grid enumerated it) still occupies a column once fed through buildWeekColumns', () => {
  const cols = buildWeekColumns(buildWeekRefs(GRID), new Set())
  const months = new Set(cols.map((c) => c.month))
  return months.has('2026-08') && months.has('2026-09') && months.size === 2
})

check('that same empty month, when EXPANDED, shows its real week columns (not one synthetic placeholder)', () => {
  const cols = buildWeekColumns(buildWeekRefs(GRID), new Set(['2026-09']))
  const septWeeks = cols.filter((c) => c.month === '2026-09' && c.kind === 'week')
  return septWeeks.length === 2 &&
    septWeeks.every((c) => c.kind === 'week' && !!c.label && c.label.startsWith('Sep'))
})

check('empty week_grid -> empty WeekRef list, no throw', () => buildWeekRefs([]).length === 0)

// ── defaultExpandedMonths ───────────────────────────────────────────────────
console.log('')
console.log('defaultExpandedMonths')

check('current month + next 2, when the current month is inside the horizon', () => {
  const months = ['2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11']
  const expanded = defaultExpandedMonths(months, new Date('2026-08-15T00:00:00Z'))
  return [...expanded].sort().join() === ['2026-08', '2026-09', '2026-10'].join()
})

check('current month at the very end of the horizon expands only what exists (no overrun, no throw)', () => {
  const months = ['2026-06', '2026-07', '2026-08']
  const expanded = defaultExpandedMonths(months, new Date('2026-08-15T00:00:00Z'))
  return [...expanded].sort().join() === ['2026-08'].join()
})

check('current month NOT in the horizon (run fully in the future) falls back to the first 3 months', () => {
  const months = ['2027-01', '2027-02', '2027-03', '2027-04']
  const expanded = defaultExpandedMonths(months, new Date('2026-08-15T00:00:00Z'))
  return [...expanded].sort().join() === ['2027-01', '2027-02', '2027-03'].join()
})

check('empty horizon -> empty expand set, no throw', () => defaultExpandedMonths([], new Date('2026-08-15T00:00:00Z')).size === 0)

console.log('')
if (failures > 0) { console.log(`${failures} check(s) FAILED`); process.exit(1) }
else console.log('All checks passed.')
