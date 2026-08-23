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
 * One deliberate deviation from the blueprint: BreakdownMatrixModal.tsx
 * additionally rejects `value < 0` (budget amounts can't be negative).
 * `parseNumericCell` here does NOT reject negatives — this module is
 * generic across MRP grids, and the Sales Forecast page (design spec
 * §6.6 page 1) has no stated rule against a negative forecast quantity
 * (e.g. a planner backing out an over-forecast via a negative adjustment
 * cell is a legitimate, if rare, entry — same reasoning the codebase
 * already applies to PO/PR unit price allowing 0/negative). If a future
 * grid needs the budget module's non-negative rule, add it as a
 * caller-supplied validator rather than hardcoding it back in here.
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

/** The only fields applyPasteUpdates() actually reads — both the rectangular
 *  PasteUpdate (below) and the full-table FullTableUpdate (further down)
 *  carry extra bookkeeping fields, but structurally satisfy this so one
 *  applyPasteUpdates() serves both paste shapes without a conversion step. */
export interface CellWrite {
  key: string
  value: number
}

export interface PasteUpdate extends CellWrite {
  rowIdx: number
  colIdx: number
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
  updates: CellWrite[],
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

// ── Row-creating "full-table" paste ─────────────────────────────────────
//
// Gap this closes: a freshly created forecast version has zero rows, so
// there is no cell to click and no anchor for the rectangular paste above
// — a planner cannot get their 18-month Excel forecast in at all without
// adding every product by hand first. This second paste shape lets a paste
// carrying a product-code column create the rows it needs, in one undo
// step, with no cell focused beforehand.
//
// Shape matches the Excel template this page generates: `Material Code |
// Name | <18 months>`. Header row is optional; when present it's used to
// align pasted month columns to the version's months by label (so a
// planner can paste a subset, or a differently-ordered range) instead of
// by position.

/** month header cells look like `YYYY-MM` — same format GridCol.id/label use for a month column (see ForecastPage's matrixCols). */
export const MONTH_LABEL_RE = /^\d{4}-\d{2}$/

/** True if `grid[0]` contains at least one YYYY-MM cell — the signal that
 *  the pasted block carries its own month header row rather than starting
 *  straight into data. */
export function hasMonthHeaderRow(grid: string[][]): boolean {
  return grid.length > 0 && grid[0].some((cell) => MONTH_LABEL_RE.test(cell.trim()))
}

/** First data row (skipping any header row) whose leading cell isn't blank
 *  — the row planFullTablePaste and isFullTablePaste both key their shape
 *  decisions off of, so a stray blank leading line doesn't derail either. */
function firstNonBlankCodeRow(dataRows: string[][]): string[] | null {
  for (const row of dataRows) {
    if ((row[0] ?? '').trim() !== '') return row
  }
  return null
}

/** A `${code}` -> resolved-row lookup, e.g. backed by mdm-api's materials
 *  list. Returns undefined for a code that isn't a known material. Only the
 *  return type's shape matters here — pasteLogic.ts stays framework/API
 *  agnostic; MatrixGrid/ForecastPage supply the actual resolver. */
export type MaterialResolver = (code: string) => { id: string; label: string } | undefined

/**
 * True if `text` should be planned as a row-creating full-table paste
 * rather than the rectangular numeric-block paste above: the pasted
 * block's first column holds a value that (a) doesn't parse as a plain
 * number and (b) resolves to a known material via `resolveMaterial`.
 * A numeric block anchored at a focused cell (today's behaviour) never
 * satisfies both, so this cleanly falls through to planPaste() otherwise.
 *
 * Decisive check runs against the first non-blank data row only (matches
 * the design spec's "the first column... holds values that resolve"
 * literally). Known limitation: if that first row's own code is a typo
 * that fails to resolve but later rows in the same paste would have
 * resolved fine, this returns false and the whole paste falls through to
 * the numeric path instead of reporting "unknown product code" for that
 * one row — a real product-code paste with a bad first row currently reads
 * as a no-op rather than a partial success. Acceptable for now: the
 * common case (planner copies straight from the template, first row is a
 * real product) is unaffected, and misclassifying an ordinary numeric
 * paste as full-table just because its first cell happens to be
 * non-numeric text would be the worse failure mode.
 */
export function isFullTablePaste(text: string, resolveMaterial: MaterialResolver): boolean {
  const grid = parseClipboardText(text)
  if (grid.length === 0) return false
  const dataRows = hasMonthHeaderRow(grid) ? grid.slice(1) : grid
  const row = firstNonBlankCodeRow(dataRows)
  if (!row) return false
  const firstCell = row[0].trim()
  if (parseNumericCell(firstCell).kind === 'value') return false
  return resolveMaterial(firstCell) !== undefined
}

/** True if `cell` reads like a product name rather than a month value —
 *  used only when the paste has no header row, to decide (once, from the
 *  first data row) whether column 1 is an optional Name column or already
 *  the first month value. A blank cell defaults to "not a name" so a
 *  blank-name paste doesn't misalign every month by one column. */
function looksLikeNameColumn(cell: string | undefined): boolean {
  const trimmed = (cell ?? '').trim()
  return trimmed !== '' && parseNumericCell(trimmed).kind !== 'value'
}

export interface FullTableUpdate extends CellWrite {
  materialCode: string
  month: string
}

export interface FullTableInvalidCell {
  key: string
  materialCode: string
  month: string
  raw: string
}

export interface FullTablePastePlan {
  kind: 'fullTable'
  /** Cell writes — same convention as PastePlan.updates (blank pasted cell = skip, not zero). */
  updates: FullTableUpdate[]
  /** Rows to create, in first-seen paste order, deduped, excluding codes already in `existingRowIds`. */
  newRows: GridRow[]
  /** Unresolved codes, deduped, in first-seen order — surfaced in the paste report so they don't silently vanish. */
  unknownCodes: string[]
  /** Count of pasted data rows dropped because their code didn't resolve (rows that DO resolve still land, even within the same paste). */
  skippedRows: number
  skippedFrozen: number
  invalidCells: FullTableInvalidCell[]
  /** Header month labels present in the paste but not one of this version's months. */
  unmatchedMonthColumns: number
  /** Data rows in the pasted block (header excluded), for the paste report and the >100 confirm gate. */
  totalRows: number
  /** totalRows * matched month columns — same "how big is this paste" role PastePlan.totalCells plays for the rectangular path. */
  totalCells: number
}

const EMPTY_FULL_TABLE_PLAN: FullTablePastePlan = {
  kind: 'fullTable',
  updates: [],
  newRows: [],
  unknownCodes: [],
  skippedRows: 0,
  skippedFrozen: 0,
  invalidCells: [],
  unmatchedMonthColumns: 0,
  totalRows: 0,
  totalCells: 0,
}

/**
 * Build the full set of row-creations and cell-writes for a full-table
 * paste. Pure and side-effect free, like planPaste() — MatrixGrid.tsx
 * decides whether to apply immediately or show the >100-cell confirmation
 * first, then commits `updates` + `newRows` as a single history step (see
 * history.ts's commitCellsAndRows) so the whole paste — rows created plus
 * values — is one undo step.
 */
export function planFullTablePaste(
  text: string,
  cols: GridCol[],
  resolveMaterial: MaterialResolver,
  existingRowIds: ReadonlySet<string>,
  frozenKeys: ReadonlySet<string>,
): FullTablePastePlan {
  const grid = parseClipboardText(text)
  if (grid.length === 0) return EMPTY_FULL_TABLE_PLAN

  const hasHeader = hasMonthHeaderRow(grid)
  const dataRows = hasHeader ? grid.slice(1) : grid
  if (dataRows.length === 0) return EMPTY_FULL_TABLE_PLAN

  // Column plan: which pasted column index holds which target month. The
  // optional Name column (if present) only matters here for figuring out
  // where the month values start — its text, if any, is never used as the
  // row label (see the resolved.label comment below).
  let monthColIndexes: Map<number, string>
  let unmatchedMonthColumns = 0

  if (hasHeader) {
    const headerRow = grid[0]
    monthColIndexes = new Map()
    for (let c = 1; c < headerRow.length; c++) {
      const cell = (headerRow[c] ?? '').trim()
      if (MONTH_LABEL_RE.test(cell)) {
        if (cols.some((col) => col.id === cell)) monthColIndexes.set(c, cell)
        else unmatchedMonthColumns++
      }
      // A non-month cell (e.g. "Name") before the first matched month
      // header needs no special handling here — it's simply never in
      // monthColIndexes, so the update loop below skips reading it.
    }
  } else {
    const anchorRow = firstNonBlankCodeRow(dataRows)
    const hasName = looksLikeNameColumn(anchorRow?.[1])
    const startIdx = hasName ? 2 : 1
    monthColIndexes = new Map(cols.map((col, i) => [startIdx + i, col.id]))
  }

  const updates: FullTableUpdate[] = []
  const invalidCells: FullTableInvalidCell[] = []
  const newRows: GridRow[] = []
  const newRowIds = new Set<string>()
  const unknownCodesSeen = new Set<string>()
  const unknownCodes: string[] = []
  let skippedRows = 0
  let skippedFrozen = 0

  for (const row of dataRows) {
    const rawCode = (row[0] ?? '').trim()
    if (rawCode === '') continue // fully blank line — not an error, just skipped

    const resolved = resolveMaterial(rawCode)
    if (!resolved) {
      if (!unknownCodesSeen.has(rawCode)) {
        unknownCodesSeen.add(rawCode)
        unknownCodes.push(rawCode)
      }
      skippedRows++
      continue
    }

    if (!existingRowIds.has(resolved.id) && !newRowIds.has(resolved.id)) {
      // The resolver's own label (materials master) is authoritative, not
      // the pasted Name text — that column only exists here to disambiguate
      // where the month values start (see nameColIndex above); a stale or
      // blank pasted name shouldn't override the real product name.
      newRowIds.add(resolved.id)
      newRows.push({ id: resolved.id, label: resolved.label })
    }

    for (const [pastedIdx, colId] of monthColIndexes) {
      const raw = row[pastedIdx]
      if (raw === undefined) continue
      const parsed = parseNumericCell(raw)
      if (parsed.kind === 'blank') continue // skip — do not zero out
      const key = cellKey(resolved.id, colId)
      if (frozenKeys.has(key)) { skippedFrozen++; continue }
      if (parsed.kind === 'invalid') {
        invalidCells.push({ key, materialCode: resolved.id, month: colId, raw })
        continue
      }
      updates.push({ key, materialCode: resolved.id, month: colId, value: parsed.value })
    }
  }

  return {
    kind: 'fullTable',
    updates,
    newRows,
    unknownCodes,
    skippedRows,
    skippedFrozen,
    invalidCells,
    unmatchedMonthColumns,
    totalRows: dataRows.length,
    totalCells: dataRows.length * monthColIndexes.size,
  }
}

/** Human-readable one-line summary for the paste report — full-table counterpart of formatPasteReport(). */
export function formatFullTablePasteReport(plan: FullTablePastePlan): string | null {
  const parts: string[] = []
  if (plan.updates.length > 0) {
    parts.push(`${plan.updates.length} cell${plan.updates.length === 1 ? '' : 's'} updated`)
  }
  if (plan.newRows.length > 0) {
    parts.push(`${plan.newRows.length} new product row${plan.newRows.length === 1 ? '' : 's'} added`)
  }
  if (plan.skippedRows > 0) {
    parts.push(`${plan.skippedRows} row${plan.skippedRows === 1 ? '' : 's'} skipped — unknown product code: ${plan.unknownCodes.join(', ')}`)
  }
  if (plan.skippedFrozen > 0) {
    parts.push(`${plan.skippedFrozen} cell${plan.skippedFrozen === 1 ? '' : 's'} skipped (frozen)`)
  }
  if (plan.invalidCells.length > 0) {
    parts.push(`${plan.invalidCells.length} cell${plan.invalidCells.length === 1 ? '' : 's'} invalid (not a number)`)
  }
  if (plan.unmatchedMonthColumns > 0) {
    parts.push(`${plan.unmatchedMonthColumns} pasted month column${plan.unmatchedMonthColumns === 1 ? '' : 's'} not in this version`)
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
