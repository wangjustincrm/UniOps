/**
 * Pure utilities for spreading a target sum across cells.
 *
 * Inspired by Oracle PBCS Grid Spread (Evenly / Proportional / Fill).
 * All three methods take a target total and an array of current cell
 * values, and return a new array of the same length whose sum equals
 * the target.
 *
 * Rounding strategy: each cell is rounded to 2 decimal places (DP).
 * Any residual from rounding is absorbed by the first cell so the
 * grand total matches exactly (no penny drift).
 */

const DP = 2

function round2(n: number): number {
  return Math.round(n * 100) / 100
}

/** Adjust the first cell so the sum exactly equals the target. */
function fixResidual(values: number[], target: number): number[] {
  if (values.length === 0) return values
  const sum = values.reduce((s, v) => s + v, 0)
  const residual = round2(target - sum)
  if (residual === 0) return values
  const out = [...values]
  out[0] = round2(out[0] + residual)
  return out
}

/**
 * Equal distribution: target / N, with residual absorbed into cell[0].
 * Used when no existing weighting makes sense (e.g. all cells are 0).
 */
export function spreadEvenly(target: number, cellCount: number): number[] {
  if (cellCount <= 0) return []
  const per = round2(target / cellCount)
  const out = Array(cellCount).fill(per)
  return fixResidual(out, target)
}

/**
 * Weighted by existing cell values. If all weights are 0, falls back to even.
 * Used to scale up/down while preserving relative proportions.
 */
export function spreadProportional(target: number, weights: number[]): number[] {
  const totalWeight = weights.reduce((s, w) => s + Math.max(w, 0), 0)
  if (totalWeight === 0) return spreadEvenly(target, weights.length)
  const out = weights.map((w) => round2((Math.max(w, 0) / totalWeight) * target))
  return fixResidual(out, target)
}

/**
 * Overwrite every cell with the same value. Sum is target * N
 * (NOT equal to `target` — this is a "set each cell to X" mode,
 * not a "spread X" mode). Caller is responsible for picking the
 * right semantics; the function returns N copies of `value`.
 */
export function spreadFill(value: number, cellCount: number): number[] {
  if (cellCount <= 0) return []
  return Array(cellCount).fill(round2(value))
}
