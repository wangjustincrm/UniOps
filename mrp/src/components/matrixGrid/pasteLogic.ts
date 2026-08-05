/**
 * Excel-style rectangular paste for MatrixGrid.
 *
 * Ported from epms/src/pages/budget/BreakdownMatrixModal.tsx lines 327-370
 * (the `window`-level paste handler), split into pure functions so the
 * parsing/planning logic can run outside React — see matrixGrid/verify.ts
 * for the one-off verification script (no test framework in this repo's
 * frontend apps yet; see task-9-report.md for why).
 *
 * Blueprint rules carried over verbatim:
 *   - only intercept multi-cell pastes (text containing \t or \n) — a
 *     single-cell paste falls through to the native input
 *   - strip \r, drop trailing blank lines, split \n then \t
 *   - numeric cleanup: trim(), strip `,` and `$`, parseFloat, reject
 *     non-finite
 *   - clip to grid bounds instead of erroring
 *   - skip computed/total cells (handled by the caller — MatrixGrid has no
 *     computed cells of its own, only frozen ones)
 *
 * Module-specific additions (design spec §6.6, page 1):
 *   - a blank pasted cell is skipped, not zeroed (protects existing data)
 *   - a cell landing on a frozen key is skipped and counted separately
 *   - a non-numeric cell is flagged and reported but does not abort the
 *     rest of the paste
 *   - the caller decides whether to confirm first when totalCells > 100
 *     (this module just reports the count; MatrixGrid.tsx does the gate)
 */

export interface GridRow {
  id: string
  label: string
}

export interface GridCol {
  id: string
  label: string
}

export function cellKey(rowId: string, colId: string): string {
  return `${rowId}::${colId}`
}

/** Inverse of cellKey() — splits on the first `::` (row ids don't contain it). */
export function parseCellKey(key: string): { rowId: string; colId: string } {
  const idx = key.indexOf('::')
  return idx === -1 ? { rowId: key, colId: '' } : { rowId: key.slice(0, idx), colId: key.slice(idx + 2) }
}

/** Anchor cell (top-left of the paste block), by grid index. */
export interface PasteAnchor {
  rowIdx: number
  colIdx: number
}

/**
 * True if `text` should be treated as a rectangular block paste. A
 * single-cell paste (no tab, no newline) returns false so it falls
 * through to whichever native input is focused.
 */
export function isMultiCellPaste(text: string): boolean {
  return !!text && (text.includes('\t') || text.includes('\n'))
}

/**
 * Strip \r, drop trailing blank line(s) (Excel copy usually ends with one),
 * split into rows of cells. Returns [] for empty input.
 */
export function parseClipboardText(text: string): string[][] {
  const cleaned = text.replace(/\r/g, '').replace(/\n+$/, '')
  if (cleaned === '') return []
  return cleaned.split('\n').map((line) => line.split('\t'))
}

export type CellParseResult =
  | { kind: 'blank' }
  | { kind: 'value'; value: number }
  | { kind: 'invalid' }

/**
 * trim() -> strip `,` and `$` -> parseFloat -> Number.isFinite check.
 * A blank cell (after trim) is its own outcome — the caller must NOT treat
 * it the same as an invalid cell: blank means "leave existing data alone",
 * invalid means "flag and report, but don't abort the rest of the paste".
 */
export function parseNumericCell(raw: string): CellParseResult {
  const trimmed = raw.trim()
  if (trimmed === '') return { kind: 'blank' }
  const stripped = trimmed.replace(/[,$]/g, '')
  const value = parseFloat(stripped)
  if (!Number.isFinite(value)) return { kind: 'invalid' }
  return { kind: 'value', value }
}

export interface PasteUpdate {
  key: string
  rowIdx: number
  colIdx: number
  value: number
}

export interface InvalidPasteCell {
  rowIdx: number
  colIdx: number
  raw: string
}

export interface PastePlan {
  /** Cells to actually write, one snapshot's worth. */
  updates: PasteUpdate[]
  /** Cells that landed on a frozen key. */
  skippedFrozen: number
  /** Cells whose text didn't parse as a number (reported, not fatal). */
  invalidCells: InvalidPasteCell[]
  /** Row/col span of the clipboard block once clipped to the grid. */
  affectedRows: number
  affectedCols: number
  /** affectedRows * affectedCols — the >100 confirmation threshold uses this. */
  totalCells: number
  /** True if the clipboard block extended past the grid and was clipped. */
  clippedRows: boolean
  clippedCols: boolean
}

const EMPTY_PLAN: PastePlan = {
  updates: [],
  skippedFrozen: 0,
  invalidCells: [],
  affectedRows: 0,
  affectedCols: 0,
  totalCells: 0,
  clippedRows: false,
  clippedCols: false,
}

/**
 * Build the full set of writes for a rectangular paste anchored at
 * `anchor`, without mutating anything — MatrixGrid.tsx decides whether to
 * apply it immediately or show a confirmation first (totalCells > 100),
 * then applies `updates` as a single history.commitCells() call so the
 * whole block is one undo step.
 */
export function planPaste(
  text: string,
  anchor: PasteAnchor,
  rows: GridRow[],
  cols: GridCol[],
  frozenKeys: ReadonlySet<string>,
): PastePlan {
  const grid = parseClipboardText(text)
  if (grid.length === 0) return EMPTY_PLAN

  const updates: PasteUpdate[] = []
  const invalidCells: InvalidPasteCell[] = []
  let skippedFrozen = 0
  let clippedRows = false
  let clippedCols = false

  const rawRowCount = grid.length
  const rawColCount = grid.reduce((m, r) => Math.max(m, r.length), 0)

  for (let r = 0; r < grid.length; r++) {
    const rowIdx = anchor.rowIdx + r
    if (rowIdx >= rows.length) {
      clippedRows = true
      continue
    }
    const cellsInRow = grid[r]
    for (let c = 0; c < cellsInRow.length; c++) {
      const colIdx = anchor.colIdx + c
      if (colIdx >= cols.length) {
        clippedCols = true
        continue
      }
      const raw = cellsInRow[c]
      const parsed = parseNumericCell(raw)
      if (parsed.kind === 'blank') continue // skip — do not zero out
      const key = cellKey(rows[rowIdx].id, cols[colIdx].id)
      if (frozenKeys.has(key)) {
        skippedFrozen++
        continue
      }
      if (parsed.kind === 'invalid') {
        invalidCells.push({ rowIdx, colIdx, raw })
        continue
      }
      updates.push({ key, rowIdx, colIdx, value: parsed.value })
    }
  }

  const affectedRows = Math.max(0, Math.min(rawRowCount, rows.length - anchor.rowIdx))
  const affectedCols = Math.max(0, Math.min(rawColCount, cols.length - anchor.colIdx))
  if (anchor.rowIdx + rawRowCount > rows.length) clippedRows = true
  if (anchor.colIdx + rawColCount > cols.length) clippedCols = true

  return {
    updates,
    skippedFrozen,
    invalidCells,
    affectedRows,
    affectedCols,
    totalCells: affectedRows * affectedCols,
    clippedRows,
    clippedCols,
  }
}

/** Apply a paste plan's updates onto a cell map, returning a new Map. 0 deletes the key (matches blueprint's "0 = no entry" sparse convention). */
export function applyPasteUpdates(
  cells: ReadonlyMap<string, number>,
  updates: PasteUpdate[],
): Map<string, number> {
  const next = new Map(cells)
  for (const u of updates) {
    if (u.value === 0) next.delete(u.key)
    else next.set(u.key, u.value)
  }
  return next
}

/** Human-readable one-line summary for the paste report (role="alert" container). */
export function formatPasteReport(plan: PastePlan): string | null {
  const parts: string[] = []
  if (plan.updates.length > 0) {
    parts.push(`${plan.updates.length} cell${plan.updates.length === 1 ? '' : 's'} updated`)
  }
  if (plan.skippedFrozen > 0) {
    parts.push(`${plan.skippedFrozen} cell${plan.skippedFrozen === 1 ? '' : 's'} skipped (frozen)`)
  }
  if (plan.invalidCells.length > 0) {
    parts.push(`${plan.invalidCells.length} cell${plan.invalidCells.length === 1 ? '' : 's'} invalid (not a number)`)
  }
  if (plan.clippedRows || plan.clippedCols) {
    parts.push('paste clipped to grid bounds')
  }
  return parts.length > 0 ? parts.join(', ') : null
}

// ── Copy (Ctrl+C -> TSV) ─────────────────────────────────────────────────

export interface RangeSelection {
  startRow: number
  startCol: number
  endRow: number
  endCol: number
}

/** Normalize a drag/shift-click selection so start <= end on both axes. */
export function normalizeRange(sel: RangeSelection): RangeSelection {
  return {
    startRow: Math.min(sel.startRow, sel.endRow),
    endRow: Math.max(sel.startRow, sel.endRow),
    startCol: Math.min(sel.startCol, sel.endCol),
    endCol: Math.max(sel.startCol, sel.endCol),
  }
}

/**
 * Serialize a rectangular selection to TSV text that pastes cleanly back
 * into Excel: blank cells (no entry in the sparse map) become empty
 * strings, not "0" — Excel/Sheets treat an empty cell and "0" differently
 * and the round-trip should not invent data.
 */
export function selectionToTsv(
  rows: GridRow[],
  cols: GridCol[],
  cells: ReadonlyMap<string, number>,
  selRaw: RangeSelection,
): string {
  const sel = normalizeRange(selRaw)
  const lines: string[] = []
  for (let r = sel.startRow; r <= sel.endRow && r < rows.length; r++) {
    const line: string[] = []
    for (let c = sel.startCol; c <= sel.endCol && c < cols.length; c++) {
      const v = cells.get(cellKey(rows[r].id, cols[c].id))
      line.push(v === undefined ? '' : String(v))
    }
    lines.push(line.join('\t'))
  }
  return lines.join('\n')
}
