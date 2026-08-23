/**
 * Verification script for closedWeek.ts — the frontend's restatement of the
 * engine's `mps_engine.py::_week_can_host`. mrp has no test framework; this
 * follows the same plain-assert convention as
 * src/components/matrixGrid/verify.ts and
 * src/pages/mps/weekColumns.verify.ts (thunked `check`, so a throw inside
 * one condition does not abort the whole run).
 *
 * Run with:  npx tsx src/pages/capacity/closedWeek.verify.ts   (from mrp/)
 */
import { closesWeek, findSkuClosure } from './closedWeek'
import type { CapacityException } from './capacityApi'

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

/** A factory-wide exception row, in the wire shape (`limit_value` is a
 *  Decimal-as-STRING — every check below feeds strings on purpose, since
 *  treating them as numbers without `Number()` is the recurring bug this
 *  repo has a memory entry for). */
function ex(over: Partial<CapacityException> = {}): CapacityException {
  return {
    id: over.id ?? 'e1',
    week_start: over.week_start ?? '2026-08-10',
    scope_type: over.scope_type ?? 'factory',
    scope_ref: over.scope_ref ?? null,
    constraint_type: over.constraint_type ?? 'max_output_qty',
    limit_value: over.limit_value ?? '0',
    uom: over.uom ?? 'KG',
    reason: over.reason ?? null,
    is_active: over.is_active ?? true,
  }
}

console.log('closesWeek')

check('max_output_qty of 0 closes the week', () =>
  closesWeek(ex({ constraint_type: 'max_output_qty', limit_value: '0' })))

check('max_output_qty of 0.000 (the Decimal-as-string the API really sends) closes it too', () =>
  closesWeek(ex({ constraint_type: 'max_output_qty', limit_value: '0.000' })))

check('a positive max_output_qty is a DE-RATE, not a closure', () =>
  !closesWeek(ex({ constraint_type: 'max_output_qty', limit_value: '20000' })))

// The half the UI was missing entirely, and the reason this module exists.
check('max_sku_count of 0 closes the week (the engine honours it; the tint used to ignore it)', () =>
  closesWeek(ex({ constraint_type: 'max_sku_count', limit_value: '0' })))

check('max_sku_count of 1 does NOT close the week (one SKU is still production)', () =>
  !closesWeek(ex({ constraint_type: 'max_sku_count', limit_value: '1' })))

check('min_output_qty of 0 closes nothing — it is a soft floor the engine never rejects on', () =>
  !closesWeek(ex({ constraint_type: 'min_output_qty', limit_value: '0' })))

check('an INACTIVE row closes nothing, whatever its value', () =>
  !closesWeek(ex({ constraint_type: 'max_output_qty', limit_value: '0', is_active: false })) &&
  !closesWeek(ex({ constraint_type: 'max_sku_count', limit_value: '0', is_active: false })))

check('a non-factory scope is ignored (matches resolve_limits_for_week\'s own filter)', () =>
  !closesWeek(ex({ scope_type: 'line', limit_value: '0' })) &&
  !closesWeek(ex({ scope_type: 'factory', scope_ref: 'LINE-1', limit_value: '0' })))

// Negative values are storable (capacity.py validates no lower bound), and
// the engine's `<= 0` / `< 1` both treat them as closed. A `=== 0` test —
// which is what both UI call sites used — gets this wrong in the unsafe
// direction: untinted column, engine places nothing.
check('a negative limit closes the week on both constraints (the engine uses <= / <, not ===)', () =>
  closesWeek(ex({ constraint_type: 'max_output_qty', limit_value: '-1' })) &&
  closesWeek(ex({ constraint_type: 'max_sku_count', limit_value: '-1' })))

console.log('')
console.log('findSkuClosure')

const ROWS: CapacityException[] = [
  ex({ id: 'a', week_start: '2026-08-10', constraint_type: 'max_output_qty', limit_value: '0' }),
  ex({ id: 'b', week_start: '2026-08-17', constraint_type: 'max_sku_count', limit_value: '0' }),
  ex({ id: 'c', week_start: '2026-08-24', constraint_type: 'max_sku_count', limit_value: '3' }),
  ex({ id: 'd', week_start: '2026-08-31', constraint_type: 'max_sku_count', limit_value: '0', is_active: false }),
]

check('finds the max_sku_count row that closes its own week', () =>
  findSkuClosure(ROWS, '2026-08-17')?.id === 'b')

check('a week closed only by max_output_qty has no SKU closure (that row is WeekDrawer\'s own)', () =>
  findSkuClosure(ROWS, '2026-08-10') === null)

check('a non-closing max_sku_count (3 SKUs allowed) is not a closure', () =>
  findSkuClosure(ROWS, '2026-08-24') === null)

check('an inactive max_sku_count of 0 is not a closure', () =>
  findSkuClosure(ROWS, '2026-08-31') === null)

check('a week with no rows at all -> null, no throw', () =>
  findSkuClosure(ROWS, '2026-09-07') === null && findSkuClosure([], '2026-08-17') === null)

console.log('')
if (failures > 0) { console.log(`${failures} check(s) FAILED`); process.exit(1) }
else console.log('All checks passed.')
