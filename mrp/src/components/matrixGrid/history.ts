/**
 * Undo/redo snapshot stack for MatrixGrid.
 *
 * Ported from epms/src/pages/budget/BreakdownMatrixModal.tsx lines 116-151
 * (Snapshot / commitSnapshot / undo / redo). Kept as pure, React-free
 * functions here so they can be exercised without mounting a component —
 * mrp has no test framework yet (neither does epms/portal/oa), so these
 * are verified via a one-off script (see matrixGrid/verify.ts) instead of
 * a permanent test file.
 *
 * State is an immutable stack of Snapshots + a cursor index. Index 0 is
 * always the pristine initial state (used by "Reset" / "modified" cell
 * colouring at the call site). Every commit truncates any redo branch
 * ahead of the cursor before pushing the new snapshot — standard linear
 * undo history semantics (same as a text editor), not a tree.
 *
 * A Snapshot also carries `extraRows` — rows a row-creating paste (see
 * pasteLogic.ts's planFullTablePaste) added that aren't in the caller's
 * `rows` prop yet. They travel in the same snapshot as the cell values
 * that came with them, so one undo step reverts rows-created-plus-values
 * together, not cells alone — see MatrixGrid.tsx's commitCellsAndRows use.
 */
import type { GridRow } from './pasteLogic'

/** One cell address — `${rowId}::${colId}`. See cellKey() in pasteLogic.ts. */
export type CellKey = string

/** Sparse map: only cells the user actually touched are stored. */
export type CellValueMap = Map<CellKey, number>

/** One immutable point in the undo/redo timeline. */
export interface Snapshot {
  cells: CellValueMap
  extraRows: GridRow[]
}

export interface History {
  stack: Snapshot[]
  idx: number
}

export function initHistory(initial: CellValueMap, extraRows: GridRow[] = []): History {
  return { stack: [{ cells: initial, extraRows }], idx: 0 }
}

export function sameMap(a: CellValueMap, b: CellValueMap): boolean {
  if (a.size !== b.size) return false
  for (const [k, v] of a) if (b.get(k) !== v) return false
  return true
}

function sameRows(a: GridRow[], b: GridRow[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i].id !== b[i].id) return false
  return true
}

export function currentCells(history: History): CellValueMap {
  return history.stack[history.idx].cells
}

export function originalCells(history: History): CellValueMap {
  return history.stack[0].cells
}

/** Rows a paste has created so far, at the current point in the undo timeline. */
export function currentExtraRows(history: History): GridRow[] {
  return history.stack[history.idx].extraRows
}

export const canUndo = (history: History): boolean => history.idx > 0
export const canRedo = (history: History): boolean => history.idx < history.stack.length - 1

/**
 * Commit a new snapshot (cells + extraRows) as one undo step. `updater`
 * receives the current snapshot and returns the next cells/extraRows —
 * this is what lets a row-creating paste land its new rows and their cell
 * values as exactly one snapshot (see MatrixGrid.tsx's applyFullTablePlan).
 *
 * No-op (returns the same History reference) if nothing actually changed,
 * so e.g. re-typing the same value doesn't pollute the undo stack.
 */
export function commitCellsAndRows(
  history: History,
  updater: (prev: Snapshot) => { cells: CellValueMap; extraRows: GridRow[] },
): History {
  const curr = history.stack[history.idx]
  const next = updater(curr)
  if (sameMap(curr.cells, next.cells) && sameRows(curr.extraRows, next.extraRows)) return history
  const truncated = history.stack.slice(0, history.idx + 1)
  return { stack: [...truncated, { cells: next.cells, extraRows: next.extraRows }], idx: truncated.length }
}

/**
 * Commit a new cell map as one undo step, leaving extraRows untouched —
 * the plain-edit / rectangular-paste path where no new row is being
 * created. `updater` receives the current cells and returns the next
 * cells — this lets a single-cell edit AND a whole rectangular paste both
 * land as exactly one snapshot (the paste handler builds the full next map
 * before calling this once).
 */
export function commitCells(
  history: History,
  updater: (prev: CellValueMap) => CellValueMap,
): History {
  return commitCellsAndRows(history, (prev) => ({ cells: updater(prev.cells), extraRows: prev.extraRows }))
}

export function undo(history: History): History {
  return canUndo(history) ? { stack: history.stack, idx: history.idx - 1 } : history
}

export function redo(history: History): History {
  return canRedo(history) ? { stack: history.stack, idx: history.idx + 1 } : history
}

export function resetToOriginal(history: History): History {
  return commitCells(history, () => originalCells(history))
}
