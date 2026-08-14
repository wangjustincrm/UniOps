/**
 * One-off verification script for weekColumns.ts's pure column-model logic
 * (month grouping / collapse / expand / horizon enumeration / default
 * expand set). mrp has no test framework — see
 * components/matrixGrid/verify.ts's header comment for the established
 * plain-assert-script convention this follows. Per task-9-brief.md Step 1,
 * the first block below (`buildWeekColumns` checks) is the brief's own
 * literal assertions; the sections after it cover the two helper functions
 * ProductionMatrix.tsx also needs (`monthsInHorizon`, `defaultExpandedMonths`)
 * that the brief did not itself enumerate.
 *
 * Run with:  npx tsx src/pages/mps/weekColumns.verify.ts   (from mrp/)
 */
import { buildWeekColumns, monthsInHorizon, defaultExpandedMonths } from './weekColumns'

let failures = 0
function check(name: string, cond: boolean) {
  if (cond) console.log(`  ok  ${name}`)
  else { failures++; console.log(`FAIL  ${name}`) }
}

// ── buildWeekColumns (task-9-brief.md Step 1, verbatim) ──────────────────
console.log('buildWeekColumns')

const WEEKS = [
  { week_start: '2026-08-03', month: '2026-08' },
  { week_start: '2026-08-10', month: '2026-08' },
  { week_start: '2026-08-17', month: '2026-08' },
  { week_start: '2026-08-24', month: '2026-08' },
  { week_start: '2026-09-07', month: '2026-09' },
]

check('an expanded month contributes one column per week', (() => {
  const cols = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09']))
  return cols.filter((c) => c.month === '2026-08' && c.kind === 'week').length === 4
})())

check('a collapsed month contributes exactly one summary column', (() => {
  const cols = buildWeekColumns(WEEKS, new Set(['2026-09']))
  const aug = cols.filter((c) => c.month === '2026-08')
  return aug.length === 1 && aug[0].kind === 'monthSummary'
})())

check('a month with no planned week still occupies a column', (() => {
  // 时间轴不能断档：10 月没有任何计划行，仍要出现在表头
  const cols = buildWeekColumns([...WEEKS, { week_start: '2026-10-05', month: '2026-10' }],
                                new Set())
  return cols.some((c) => c.month === '2026-10')
})())

check('collapsing then expanding returns the original column set', (() => {
  const a = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09'])).map((c) => c.id).join()
  const b = buildWeekColumns(WEEKS, new Set(['2026-09']))
  const c = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09'])).map((x) => x.id).join()
  return a === c && b.length < WEEKS.length
})())

// ── buildWeekColumns: additional coverage beyond the brief's 4 assertions ──
console.log('')
console.log('buildWeekColumns (additional)')

check('months come out in ascending chronological order regardless of input order', (() => {
  const shuffled = [
    { week_start: '2026-09-07', month: '2026-09' },
    { week_start: '2026-07-06', month: '2026-07' },
    { week_start: '2026-08-03', month: '2026-08' },
  ]
  const cols = buildWeekColumns(shuffled, new Set(['2026-07', '2026-08', '2026-09']))
  return cols.map((c) => c.month).join() === ['2026-07', '2026-08', '2026-09'].join()
})())

check('duplicate week_start within a month (two lines sharing a week) collapses to ONE week column', (() => {
  const dup = [
    { week_start: '2026-08-03', month: '2026-08', label: 'Aug W1' },
    { week_start: '2026-08-03', month: '2026-08', label: 'Aug W1' },
  ]
  const cols = buildWeekColumns(dup, new Set(['2026-08']))
  return cols.length === 1 && cols[0].kind === 'week'
})())

check('week columns within an expanded month are sorted chronologically', (() => {
  const outOfOrder = [
    { week_start: '2026-08-24', month: '2026-08' },
    { week_start: '2026-08-03', month: '2026-08' },
    { week_start: '2026-08-10', month: '2026-08' },
  ]
  const cols = buildWeekColumns(outOfOrder, new Set(['2026-08']))
  return cols.map((c) => (c.kind === 'week' ? c.week_start : '')).join() ===
    ['2026-08-03', '2026-08-10', '2026-08-24'].join()
})())

check('a real week_label survives onto its week Column', (() => {
  const cols = buildWeekColumns(
    [{ week_start: '2026-08-03', month: '2026-08', label: 'Aug W1 · Aug 3–9' }],
    new Set(['2026-08']),
  )
  return cols[0].kind === 'week' && cols[0].label === 'Aug W1 · Aug 3–9'
})())

check('empty input -> empty output, no throw', buildWeekColumns([], new Set()).length === 0)

check('column ids are stable and unique across a mixed expand/collapse set', (() => {
  const cols = buildWeekColumns(WEEKS, new Set(['2026-08']))
  const ids = cols.map((c) => c.id)
  return new Set(ids).size === ids.length
})())

// ── monthsInHorizon ────────────────────────────────────────────────────────
console.log('')
console.log('monthsInHorizon')

check('6 months from 2026-08 -> Aug through Jan, wrapping the year', (() => {
  const months = monthsInHorizon('2026-08', 6)
  return months.join() === ['2026-08', '2026-09', '2026-10', '2026-11', '2026-12', '2027-01'].join()
})())

check('0 months -> empty list', monthsInHorizon('2026-08', 0).length === 0)

check('1 month -> just the start month, unchanged', (() => {
  const months = monthsInHorizon('2026-12', 1)
  return months.length === 1 && months[0] === '2026-12'
})())

check('18-month horizon (the real MRP horizon length) starting January spans two calendar years correctly', (() => {
  const months = monthsInHorizon('2026-01', 18)
  return months.length === 18 && months[0] === '2026-01' && months[17] === '2027-06'
})())

// ── defaultExpandedMonths ───────────────────────────────────────────────────
console.log('')
console.log('defaultExpandedMonths')

check('current month + next 2, when the current month is inside the horizon', (() => {
  const months = ['2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11']
  const expanded = defaultExpandedMonths(months, new Date('2026-08-15T00:00:00Z'))
  return [...expanded].sort().join() === ['2026-08', '2026-09', '2026-10'].join()
})())

check('current month at the very end of the horizon expands only what exists (no overrun, no throw)', (() => {
  const months = ['2026-06', '2026-07', '2026-08']
  const expanded = defaultExpandedMonths(months, new Date('2026-08-15T00:00:00Z'))
  return [...expanded].sort().join() === ['2026-08'].join()
})())

check('current month NOT in the horizon (run fully in the future) falls back to the first 3 months', (() => {
  const months = ['2027-01', '2027-02', '2027-03', '2027-04']
  const expanded = defaultExpandedMonths(months, new Date('2026-08-15T00:00:00Z'))
  return [...expanded].sort().join() === ['2027-01', '2027-02', '2027-03'].join()
})())

check('empty horizon -> empty expand set, no throw', defaultExpandedMonths([], new Date('2026-08-15T00:00:00Z')).size === 0)

console.log('')
if (failures > 0) { console.log(`${failures} check(s) FAILED`); process.exit(1) }
else console.log('All checks passed.')
