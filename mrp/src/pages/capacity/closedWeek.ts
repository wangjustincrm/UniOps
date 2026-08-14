// closedWeek — "is this week closed to all production?", the frontend's
// single restatement of the engine's own rule.
//
// Deliberately a separate module from capacityApi.ts, which imports
// `@/lib/api`: that alias cannot be resolved by `npx tsx`, so anything
// living beside the HTTP client is unreachable to this repo's only frontend
// test harness (the plain-assert `*.verify.ts` convention — see
// components/matrixGrid/verify.ts's header). This branch has already had
// two review findings of the form "the rule lives in a place the verify
// script cannot import"; this file exists so that does not happen a third
// time. It has no runtime imports at all — the `CapacityException` type
// comes in as a type-only import, which is erased.
//
// Verified by closedWeek.verify.ts:
//   npx tsx src/pages/capacity/closedWeek.verify.ts   (from mrp/)
import type { CapacityException } from './capacityApi'

/** Does this ONE exception row close its week to all production?
 *
 * The engine's predicate is `mps_engine.py::_week_can_host`, and it closes
 * a week on EITHER `max_output_qty <= 0` OR `max_sku_count < 1`:
 *
 * ```python
 * if limits.max_output_qty is not None and limits.max_output_qty <= 0: return False
 * if limits.max_sku_count  is not None and limits.max_sku_count  < 1:  return False
 * ```
 *
 * The UI used to key only on `max_output_qty === 0`, in two places (the
 * matrix tint and WeekDrawer's checkbox). `Max SKUs / week` is offered in
 * the Week Exceptions dropdown and its value field accepts 0, so a planner
 * could close a week in a way the engine fully honoured, the matrix never
 * tinted, and WeekDrawer then offered to "mark it as a maintenance week" —
 * writing a SECOND row for the same week under a different
 * `constraint_type`, which the partial unique index happily allows.
 *
 * `min_output_qty` is a SOFT floor and closes nothing — it is read by
 * neither `fits()` nor `_week_can_host` (see `CapacityLimits`' own field
 * comment: "never rejects a placement or creates a capacity gap").
 *
 * Scope: factory-wide rows only (`scope_ref === null`), matching
 * `resolve_limits_for_week`'s own filter — the partial unique index only
 * covers that set, and no other scope exists this phase.
 *
 * `limit_value` arrives as a Decimal-as-string, so the `Number()` is
 * required, not cosmetic (feedback_uniops_decimal_as_string). An inactive
 * row closes nothing.
 */
export function closesWeek(e: CapacityException): boolean {
  if (!e.is_active || e.scope_type !== 'factory' || (e.scope_ref ?? null) !== null) return false
  const value = Number(e.limit_value)
  if (e.constraint_type === 'max_output_qty') return value <= 0
  if (e.constraint_type === 'max_sku_count') return value < 1
  return false
}

/** The `max_sku_count` exception CLOSING this week, if there is one.
 *
 * WeekDrawer owns exactly one row — the factory-wide `max_output_qty` one —
 * so a week closed this other way is a week whose state it can neither
 * express nor undo. It refuses to write rather than adding a duplicate
 * shutdown row; this is how it finds out. */
export function findSkuClosure(
  exceptions: CapacityException[], weekStart: string,
): CapacityException | null {
  return exceptions.find((e) =>
    e.week_start === weekStart && e.constraint_type === 'max_sku_count' && closesWeek(e),
  ) ?? null
}
