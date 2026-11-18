/**
 * Matrix-style breakdown editor — replaces the legacy "Add Combination" list.
 *
 * Layout rules (driven by factor count, ranked by sort_order):
 *   1 factor  →  rows + a single Amount column
 *   2 factors →  rows × cols, with Row Total and Col Total
 *   3 factors →  rows × (cols × subcols), with per-col Sum, Row Total, Col Total
 *
 * The cell's plan_line.amount = SUM(breakdowns). On Save we PUT the full new
 * breakdown list atomically — backend deletes existing rows and inserts these,
 * then recomputes line.amount in the same transaction.
 *
 * Design inspired by Oracle PBCS Grid Spread (color-coded computed cells),
 * Workday Adaptive Planning (Reference sidebar with last year + actuals),
 * and Vena (Excel paste compatibility, keyboard navigation).
 */
import { useState, useMemo, useEffect, useCallback, useRef } from 'react'
import { Save, RotateCcw, Sparkles, ChevronDown, Copy, Info, Undo2, Redo2, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount } from '@/lib/utils'
import { useFactors, useReplaceBreakdowns, useBaseline } from '@/hooks/useBudget'
import type { ApiBreakdown, ApiFactor, ApiFactorValue } from '@/services/budget'
import { spreadEvenly, spreadProportional, spreadFill } from './spreadUtils'

import { Checkbox, DropdownPortal, Input, Modal, TBody, THead, Table, Td, Textarea, Th, Tr, useAnchorRect } from '@uniops/shell'
const MONTHS = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MAX_FACTORS_IN_MATRIX = 3

interface Props {
  planId: string
  accountId: string
  accountCode: string
  accountName: string
  month: number
  currentTotal: number
  currentBreakdowns: ApiBreakdown[]
  editable: boolean
  onClose: () => void
}

/** One cell address in the matrix — exactly the factor_combo it represents. */
type CellKey = string  // JSON-stringified factor_combo for stable indexing

/** Local edit state — sparse map: only cells the user typed in are stored. */
type CellMap = Map<CellKey, number>
type NoteMap = Map<CellKey, string>

/** One immutable point in the undo/redo timeline. */
type Snapshot = { cells: CellMap; notes: NoteMap }

function comboKey(combo: Record<string, string>): CellKey {
  // Stable JSON-stringify: sort keys so the same combo always produces the
  // same string regardless of insertion order.
  const sorted: Record<string, string> = {}
  for (const k of Object.keys(combo).sort()) sorted[k] = combo[k]
  return JSON.stringify(sorted)
}

/** Build cellMap from existing breakdowns (filter out keys outside the matrix). */
function initialCellMap(breakdowns: ApiBreakdown[]): CellMap {
  const m: CellMap = new Map()
  for (const b of breakdowns) {
    m.set(comboKey(b.factor_combo), Number(b.amount))
  }
  return m
}

/** Build noteMap from existing breakdowns. */
function initialNoteMap(breakdowns: ApiBreakdown[]): NoteMap {
  const m: NoteMap = new Map()
  for (const b of breakdowns) {
    if (b.notes && b.notes.trim()) m.set(comboKey(b.factor_combo), b.notes)
  }
  return m
}

function sameMap<V>(a: Map<CellKey, V>, b: Map<CellKey, V>): boolean {
  if (a.size !== b.size) return false
  for (const [k, v] of a) if (b.get(k) !== v) return false
  return true
}

// Flat list of "leaf columns" for the right-pointing axis.
//   1-factor case: 1 column ("Amount")
//   2-factor case: N cols (one per col value)
//   3-factor case: N*M cols + per-col-group "Sum"
// Shared between the matrix table and the Excel-paste handler (which needs to
// translate the focused cell to a leaf-col index so it knows where to start).
type LeafCol =
  | { kind: 'amount' }
  | { kind: 'col'; col: ApiFactorValue }
  | { kind: 'sub'; col: ApiFactorValue; sub: ApiFactorValue }
  | { kind: 'col_sum'; col: ApiFactorValue }

// ── Main ──────────────────────────────────────────────────────────────────────

export function BreakdownMatrixModal({
  planId, accountId, accountCode, accountName, month,
  currentBreakdowns, editable, onClose,
}: Props) {
  const { data: factorsRaw = [] } = useFactors(accountId)
  const replaceMut = useReplaceBreakdowns(planId)
  const { data: baseline } = useBaseline(planId, accountId, month)

  // First 3 active factors by sort_order — extra are grandfathered (read-only badge).
  const factors = useMemo(() => {
    const active = factorsRaw.filter((f) => f.is_active)
    return [...active]
      .sort((a, b) => a.sort_order - b.sort_order || a.factor_code.localeCompare(b.factor_code))
      .slice(0, MAX_FACTORS_IN_MATRIX)
  }, [factorsRaw])

  const [showInactive, setShowInactive] = useState(false)
  // History stack drives undo/redo. Every committed edit pushes a new Snapshot
  // and drops any redo branch ahead of the current index. Snapshot at index 0
  // is the pristine initial state — used by Reset and "modified cell" colouring.
  const [history, setHistory] = useState<{ stack: Snapshot[]; idx: number }>(() => ({
    stack: [{
      cells: initialCellMap(currentBreakdowns),
      notes: initialNoteMap(currentBreakdowns),
    }],
    idx: 0,
  }))
  const cellMap = history.stack[history.idx].cells
  const noteMap = history.stack[history.idx].notes
  const originalMap = history.stack[0].cells
  const originalNoteMap = history.stack[0].notes
  const canUndo = history.idx > 0
  const canRedo = history.idx < history.stack.length - 1

  const [focusedKey, setFocusedKey] = useState<CellKey | null>(null)
  const [spreadOpen, setSpreadOpen] = useState(false)
  const spreadAnchorRef = useRef<HTMLDivElement>(null)
  const spreadRect = useAnchorRect(spreadOpen, spreadAnchorRef)
  const [saveError, setSaveError] = useState<string | null>(null)

  const commitSnapshot = useCallback((updater: (prev: Snapshot) => Snapshot) => {
    setHistory((h) => {
      const curr = h.stack[h.idx]
      const next = updater(curr)
      if (sameMap(curr.cells, next.cells) && sameMap(curr.notes, next.notes)) return h
      const truncated = h.stack.slice(0, h.idx + 1)
      return { stack: [...truncated, next], idx: h.idx + 1 }
    })
  }, [])

  const undo = useCallback(() => {
    setHistory((h) => h.idx > 0 ? { stack: h.stack, idx: h.idx - 1 } : h)
  }, [])

  const redo = useCallback(() => {
    setHistory((h) => h.idx < h.stack.length - 1 ? { stack: h.stack, idx: h.idx + 1 } : h)
  }, [])

  // Visible values per factor (active or all, depending on toggle).
  const visibleValues = useCallback((f: ApiFactor): ApiFactorValue[] => {
    const sorted = [...f.values].sort((a, b) => a.sort_order - b.sort_order || a.value_code.localeCompare(b.value_code))
    return showInactive ? sorted : sorted.filter((v) => v.is_active)
  }, [showInactive])

  // Layout dimensions
  const rowFactor = factors[0]  // Primary
  const colFactor = factors[1]  // 2nd
  const subColFactor = factors[2]  // 3rd

  const rowValues = rowFactor ? visibleValues(rowFactor) : []
  const colValues = colFactor ? visibleValues(colFactor) : []
  const subColValues = subColFactor ? visibleValues(subColFactor) : []

  // Compose a combo for a cell coordinate. Returns null if any required axis is missing.
  const comboFor = useCallback((
    row: ApiFactorValue | null,
    col: ApiFactorValue | null,
    sub: ApiFactorValue | null,
  ): Record<string, string> | null => {
    if (!rowFactor || !row) return null
    const combo: Record<string, string> = { [rowFactor.factor_code]: row.value_code }
    if (colFactor) {
      if (!col) return null
      combo[colFactor.factor_code] = col.value_code
    }
    if (subColFactor) {
      if (!sub) return null
      combo[subColFactor.factor_code] = sub.value_code
    }
    return combo
  }, [rowFactor, colFactor, subColFactor])

  // Helpers
  const cellValue = useCallback((combo: Record<string, string>): number => {
    return cellMap.get(comboKey(combo)) ?? 0
  }, [cellMap])

  const setCellValue = useCallback((combo: Record<string, string>, value: number) => {
    const key = comboKey(combo)
    commitSnapshot((curr) => {
      const newCells = new Map(curr.cells)
      if (value === 0) newCells.delete(key)
      else newCells.set(key, value)
      return { cells: newCells, notes: curr.notes }
    })
  }, [commitSnapshot])

  const setNote = useCallback((combo: Record<string, string>, note: string) => {
    const key = comboKey(combo)
    const trimmed = note.trim()
    commitSnapshot((curr) => {
      const newNotes = new Map(curr.notes)
      if (trimmed === '') newNotes.delete(key)
      else newNotes.set(key, trimmed)
      return { cells: curr.cells, notes: newNotes }
    })
  }, [commitSnapshot])

  const isCellModified = useCallback((combo: Record<string, string>): boolean => {
    const key = comboKey(combo)
    const valChanged = (cellMap.get(key) ?? 0) !== (originalMap.get(key) ?? 0)
    const noteChanged = (noteMap.get(key) ?? '') !== (originalNoteMap.get(key) ?? '')
    return valChanged || noteChanged
  }, [cellMap, originalMap, noteMap, originalNoteMap])

  const cellHasNote = useCallback((combo: Record<string, string>): boolean => {
    return noteMap.has(comboKey(combo))
  }, [noteMap])

  // ── Totals (recomputed on every render via memoization) ─────────────────────

  const rowTotal = useCallback((row: ApiFactorValue): number => {
    let sum = 0
    if (!colFactor) {
      const c = comboFor(row, null, null)
      if (c) sum += cellValue(c)
    } else {
      for (const cv of colValues) {
        if (!subColFactor) {
          const c = comboFor(row, cv, null)
          if (c) sum += cellValue(c)
        } else {
          for (const sv of subColValues) {
            const c = comboFor(row, cv, sv)
            if (c) sum += cellValue(c)
          }
        }
      }
    }
    return sum
  }, [colFactor, colValues, subColFactor, subColValues, comboFor, cellValue])

  const colTotal = useCallback((col: ApiFactorValue): number => {
    let sum = 0
    for (const rv of rowValues) {
      if (!subColFactor) {
        const c = comboFor(rv, col, null)
        if (c) sum += cellValue(c)
      } else {
        for (const sv of subColValues) {
          const c = comboFor(rv, col, sv)
          if (c) sum += cellValue(c)
        }
      }
    }
    return sum
  }, [rowValues, subColFactor, subColValues, comboFor, cellValue])

  const subColTotal = useCallback((col: ApiFactorValue, sub: ApiFactorValue): number => {
    let sum = 0
    for (const rv of rowValues) {
      const c = comboFor(rv, col, sub)
      if (c) sum += cellValue(c)
    }
    return sum
  }, [rowValues, comboFor, cellValue])

  const grandTotal = useMemo(() => {
    let sum = 0
    for (const v of cellMap.values()) sum += v
    return sum
  }, [cellMap])

  const originalTotal = useMemo(() => {
    let sum = 0
    for (const v of originalMap.values()) sum += v
    return sum
  }, [originalMap])

  const netChange = grandTotal - originalTotal

  // ── Leaf column layout (shared with MatrixTable + Excel paste) ──────────────

  const leafCols: LeafCol[] = useMemo(() => {
    if (!colFactor) return [{ kind: 'amount' }]
    if (!subColFactor) return colValues.map((cv) => ({ kind: 'col' as const, col: cv }))
    const out: LeafCol[] = []
    for (const cv of colValues) {
      for (const sv of subColValues) out.push({ kind: 'sub', col: cv, sub: sv })
      out.push({ kind: 'col_sum', col: cv })
    }
    return out
  }, [colFactor, colValues, subColFactor, subColValues])

  // Map the focused cell to (rowIdx, leafColIdx) — the top-left anchor used
  // when pasting a rectangular block from Excel.
  const focusedAnchor = useMemo(() => {
    if (!focusedKey || !rowFactor) return null
    let combo: Record<string, string>
    try { combo = JSON.parse(focusedKey) } catch { return null }
    const rowCode = combo[rowFactor.factor_code]
    const rowIdx = rowValues.findIndex((v) => v.value_code === rowCode)
    if (rowIdx < 0) return null
    let leafIdx = -1
    if (!colFactor) {
      leafIdx = 0
    } else if (!subColFactor) {
      const colCode = combo[colFactor.factor_code]
      leafIdx = leafCols.findIndex((lc) => lc.kind === 'col' && lc.col.value_code === colCode)
    } else {
      const colCode = combo[colFactor.factor_code]
      const subCode = combo[subColFactor.factor_code]
      leafIdx = leafCols.findIndex((lc) =>
        lc.kind === 'sub' && lc.col.value_code === colCode && lc.sub.value_code === subCode,
      )
    }
    if (leafIdx < 0) return null
    return { rowIdx, leafIdx }
  }, [focusedKey, rowFactor, colFactor, subColFactor, rowValues, leafCols])

  // ── Excel paste — rectangular block at focused cell ─────────────────────────

  useEffect(() => {
    if (!editable) return
    const handler = (e: ClipboardEvent) => {
      if (!focusedAnchor) return
      const text = e.clipboardData?.getData('text/plain') ?? ''
      // Only intercept multi-cell pastes — single-cell paste falls through to
      // whichever input is focused so users keep the native behaviour.
      if (!text || (!text.includes('\t') && !text.includes('\n'))) return
      e.preventDefault()
      const rows = text.replace(/\r/g, '').replace(/\n+$/, '').split('\n')
      const updates: Array<{ key: CellKey; value: number }> = []
      for (let r = 0; r < rows.length && focusedAnchor.rowIdx + r < rowValues.length; r++) {
        const cells = rows[r].split('\t')
        for (let c = 0; c < cells.length && focusedAnchor.leafIdx + c < leafCols.length; c++) {
          const raw = cells[c].trim().replace(/[,$]/g, '')
          if (!raw) continue
          const value = parseFloat(raw)
          if (!Number.isFinite(value) || value < 0) continue
          const leafCol = leafCols[focusedAnchor.leafIdx + c]
          if (leafCol.kind === 'col_sum') continue  // computed cell — skip
          const rv = rowValues[focusedAnchor.rowIdx + r]
          if (!rv.is_active && !showInactive) continue
          let combo: Record<string, string> | null
          if (leafCol.kind === 'amount') combo = comboFor(rv, null, null)
          else if (leafCol.kind === 'col') combo = comboFor(rv, leafCol.col, null)
          else combo = comboFor(rv, leafCol.col, leafCol.sub)
          if (combo) updates.push({ key: comboKey(combo), value })
        }
      }
      if (updates.length === 0) return
      commitSnapshot((curr) => {
        const newCells = new Map(curr.cells)
        for (const u of updates) {
          if (u.value === 0) newCells.delete(u.key)
          else newCells.set(u.key, u.value)
        }
        return { cells: newCells, notes: curr.notes }
      })
    }
    window.addEventListener('paste', handler)
    return () => window.removeEventListener('paste', handler)
  }, [editable, focusedAnchor, rowValues, leafCols, comboFor, showInactive, commitSnapshot])

  // ── Save / Cancel ───────────────────────────────────────────────────────────

  const handleSave = async () => {
    setSaveError(null)
    const items: Array<{ factor_combo: Record<string, string>; amount: number; notes?: string }> = []
    // Union of keys with values + keys with notes — a cell with a note but no
    // amount should still be saved so the note isn't silently dropped.
    const keys = new Set<CellKey>([...cellMap.keys(), ...noteMap.keys()])
    for (const key of keys) {
      const amount = cellMap.get(key) ?? 0
      const note = noteMap.get(key)
      if (amount > 0 || (note && note.trim())) {
        const item: { factor_combo: Record<string, string>; amount: number; notes?: string } = {
          factor_combo: JSON.parse(key),
          amount,
        }
        if (note && note.trim()) item.notes = note.trim()
        items.push(item)
      }
    }
    try {
      await replaceMut.mutateAsync({ accountId, month, items })
      onClose()
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  const handleReset = () => {
    setHistory((h) => {
      const initial = h.stack[0]
      const curr = h.stack[h.idx]
      if (sameMap(curr.cells, initial.cells) && sameMap(curr.notes, initial.notes)) return h
      const truncated = h.stack.slice(0, h.idx + 1)
      return {
        stack: [...truncated, { cells: initial.cells, notes: initial.notes }],
        idx: h.idx + 1,
      }
    })
  }

  const handleCopyFromLastYear = () => {
    if (!baseline?.last_year_breakdowns?.length) return
    commitSnapshot(() => {
      const newCells: CellMap = new Map()
      const newNotes: NoteMap = new Map()
      for (const bd of baseline.last_year_breakdowns) {
        const key = comboKey(bd.factor_combo)
        newCells.set(key, Number(bd.amount))
        if (bd.notes && bd.notes.trim()) newNotes.set(key, bd.notes)
      }
      return { cells: newCells, notes: newNotes }
    })
  }

  // ── Spread (Evenly / Proportional / Fill) ───────────────────────────────────

  type SpreadScope = 'row' | 'col' | 'all'
  type SpreadMethod = 'evenly' | 'proportional' | 'fill'

  const applySpread = (scope: SpreadScope, method: SpreadMethod, target: number) => {
    if (!Number.isFinite(target) || target < 0) return
    const cells: Array<{ combo: Record<string, string>; current: number }> = []

    // Collect cells in the chosen scope.
    if (scope === 'row') {
      if (!focusedKey) return
      const parsed = JSON.parse(focusedKey)
      const rowCode = parsed[rowFactor!.factor_code]
      const rv = rowValues.find((v) => v.value_code === rowCode)
      if (!rv) return
      if (!colFactor) {
        const c = comboFor(rv, null, null)
        if (c) cells.push({ combo: c, current: cellValue(c) })
      } else {
        for (const cv of colValues) {
          if (!subColFactor) {
            const c = comboFor(rv, cv, null)
            if (c) cells.push({ combo: c, current: cellValue(c) })
          } else {
            for (const sv of subColValues) {
              const c = comboFor(rv, cv, sv)
              if (c) cells.push({ combo: c, current: cellValue(c) })
            }
          }
        }
      }
    } else if (scope === 'col') {
      if (!focusedKey || !colFactor) return
      const parsed = JSON.parse(focusedKey)
      const colCode = parsed[colFactor.factor_code]
      const cv = colValues.find((v) => v.value_code === colCode)
      if (!cv) return
      for (const rv of rowValues) {
        if (!subColFactor) {
          const c = comboFor(rv, cv, null)
          if (c) cells.push({ combo: c, current: cellValue(c) })
        } else {
          for (const sv of subColValues) {
            const c = comboFor(rv, cv, sv)
            if (c) cells.push({ combo: c, current: cellValue(c) })
          }
        }
      }
    } else {
      // all
      for (const rv of rowValues) {
        if (!colFactor) {
          const c = comboFor(rv, null, null)
          if (c) cells.push({ combo: c, current: cellValue(c) })
        } else {
          for (const cv of colValues) {
            if (!subColFactor) {
              const c = comboFor(rv, cv, null)
              if (c) cells.push({ combo: c, current: cellValue(c) })
            } else {
              for (const sv of subColValues) {
                const c = comboFor(rv, cv, sv)
                if (c) cells.push({ combo: c, current: cellValue(c) })
              }
            }
          }
        }
      }
    }

    if (cells.length === 0) return
    let newVals: number[]
    if (method === 'evenly') newVals = spreadEvenly(target, cells.length)
    else if (method === 'proportional') newVals = spreadProportional(target, cells.map((c) => c.current))
    else newVals = spreadFill(target, cells.length)

    commitSnapshot((curr) => {
      const newCells = new Map(curr.cells)
      cells.forEach((c, i) => {
        const v = newVals[i]
        if (v === 0) newCells.delete(comboKey(c.combo))
        else newCells.set(comboKey(c.combo), v)
      })
      return { cells: newCells, notes: curr.notes }
    })
    setSpreadOpen(false)
  }

  // ── Keyboard: Cmd+Enter saves, Esc closes ──────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        if (editable) handleSave()
      } else if (editable && (e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        undo()
      } else if (editable && (e.metaKey || e.ctrlKey) && (
        (e.shiftKey && e.key.toLowerCase() === 'z') || e.key.toLowerCase() === 'y'
      )) {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editable, cellMap, noteMap])

  // ── Render ─────────────────────────────────────────────────────────────────

  if (factors.length === 0) {
    return (
      <ModalShell title={`Breakdown — ${MONTHS[month]}`} subtitle={`${accountCode} · ${accountName}`} onClose={onClose}>
        <div className="p-6 text-center text-sm text-fg-muted">
          No active factors configured for this Account. Configure factors on the Catalog page first.
        </div>
      </ModalShell>
    )
  }

  const grandFathered = factorsRaw.filter((f) => f.is_active).length > MAX_FACTORS_IN_MATRIX

  return (
    <ModalShell
      title={`Budget Breakdown — ${MONTHS[month]}`}
      subtitle={`${accountCode} · ${accountName}`}
      onClose={onClose}
      wide
    >
      <div className="flex flex-1 overflow-hidden">
        {/* Main panel */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Tool strip */}
          <div className="border-b border-line bg-surface-subtle px-4 py-2.5 flex items-center gap-3 flex-wrap shrink-0">
            <span className="text-xs text-fg-muted">
              <strong>Rows</strong>: {rowFactor.factor_name}
              {colFactor && <> · <strong>Cols</strong>: {colFactor.factor_name}</>}
              {subColFactor && <> · <strong>Sub</strong>: {subColFactor.factor_name}</>}
            </span>
            <div className="flex-1" />
            {editable && (
              <>
                <div className="relative" ref={spreadAnchorRef}>
                  <Button variant="secondary" size="xs"
                    type="button"
                    onClick={() => setSpreadOpen((v) => !v)}
                   
                    title="Distribute a value across row, column, or all cells"
                  >
                    <Sparkles className="h-3.5 w-3.5" /> Spread
                    <ChevronDown className="h-3 w-3" />
                  </Button>
                  {spreadOpen && spreadRect && (
                    <SpreadMenu
                      anchorRect={spreadRect}
                      onClose={() => setSpreadOpen(false)}
                      onApply={applySpread}
                      hasFocus={!!focusedKey}
                      hasColAxis={!!colFactor}
                    />
                  )}
                </div>
                {baseline?.last_year_breakdowns?.length ? (
                  <Button variant="secondary" size="xs"
                    type="button"
                    onClick={handleCopyFromLastYear}
                   
                    title="Replace matrix with last year same-month breakdowns"
                  >
                    <Copy className="h-3.5 w-3.5" /> Copy Last Year
                  </Button>
                ) : null}
                <Button variant="secondary" size="xs"
                  type="button"
                  onClick={undo}
                  disabled={!canUndo}
                 
                  title={`Undo (${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Z)`}
                >
                  <Undo2 className="h-3.5 w-3.5" />
                </Button>
                <Button variant="secondary" size="xs"
                  type="button"
                  onClick={redo}
                  disabled={!canRedo}
                 
                  title={`Redo (${navigator.platform.includes('Mac') ? '⌘⇧' : 'Ctrl+Shift'}+Z)`}
                >
                  <Redo2 className="h-3.5 w-3.5" />
                </Button>
                <Button variant="secondary" size="xs"
                  type="button"
                  onClick={handleReset}
                  disabled={netChange === 0}
                 
                  title="Discard unsaved edits"
                >
                  <RotateCcw className="h-3.5 w-3.5" /> Reset
                </Button>
              </>
            )}
            <label className="inline-flex items-center gap-1.5 text-2xs text-fg-secondary cursor-pointer select-none">
              <Checkbox
               
                checked={showInactive}
                onChange={(e) => setShowInactive(e.target.checked)}
               
              />
              Show inactive
            </label>
          </div>

          {/* Grandfathered notice */}
          {grandFathered && (
            <div className="bg-warning-soft border-b border-warning-line px-4 py-2 text-xs text-warning-fg">
              This Account has more than 3 factors. The matrix uses the first 3 by sort order;
              additional factors are preserved in the database but not editable here.
            </div>
          )}

          {/* Matrix table */}
          <div className="flex-1 overflow-auto">
            <MatrixTable
              rowFactor={rowFactor}
              colFactor={colFactor ?? null}
              subColFactor={subColFactor ?? null}
              rowValues={rowValues}
              colValues={colValues}
              subColValues={subColValues}
              leafCols={leafCols}
              editable={editable}
              comboFor={comboFor}
              cellValue={cellValue}
              setCellValue={setCellValue}
              isCellModified={isCellModified}
              cellHasNote={cellHasNote}
              rowTotal={rowTotal}
              colTotal={colTotal}
              subColTotal={subColTotal}
              grandTotal={grandTotal}
              focusedKey={focusedKey}
              setFocusedKey={setFocusedKey}
            />
          </div>

          {/* Bottom status bar */}
          <div className="border-t border-line bg-surface-subtle px-4 py-2 flex items-center gap-3 text-xs text-fg-secondary shrink-0">
            <span>
              Net change:{' '}
              <span className={cn(
                'font-mono font-semibold',
                netChange > 0 ? 'text-success-fg' : netChange < 0 ? 'text-danger-fg' : 'text-fg-muted',
              )}>
                {netChange >= 0 ? '+' : ''}{formatAmount(netChange, 'CAD')}
              </span>
            </span>
            <span className="text-fg-faint">|</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-3 w-3 rounded border border-line bg-surface" /> editable
              <span className="ml-2 inline-block h-3 w-3 rounded border border-line bg-surface-sunken" /> computed
              <span className="ml-2 inline-block h-3 w-3 rounded border border-warning-line bg-warning-soft" /> modified
            </span>
            <div className="flex-1" />
            <span className="text-fg-muted">
              Tab moves · Esc closes · {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Enter saves · {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Z undo · Paste from Excel
            </span>
          </div>

          {saveError && (
            <div className="border-t border-danger-line bg-danger-soft px-4 py-2 text-xs text-danger-fg shrink-0">
              {saveError}
            </div>
          )}
        </div>

        {/* Reference sidebar */}
        <BaselineSidebar
          baseline={baseline}
          focusedKey={focusedKey}
          cellValue={(combo) => cellValue(combo)}
          cellNote={(combo) => noteMap.get(comboKey(combo)) ?? ''}
          onChangeNote={(combo, note) => setNote(combo, note)}
          editable={editable}
          grandTotal={grandTotal}
        />
      </div>

      {/* Footer */}
      <div className="border-t border-line px-5 py-3 flex items-center justify-end gap-2 shrink-0">
        <Button size="sm" variant="secondary" onClick={onClose}>Cancel</Button>
        {editable && (
          <Button size="sm" onClick={handleSave} disabled={replaceMut.isPending}>
            <Save className="h-3.5 w-3.5" />
            {replaceMut.isPending ? 'Saving…' : 'Save Matrix'}
          </Button>
        )}
      </div>
    </ModalShell>
  )
}

// ── Modal shell ──────────────────────────────────────────────────────────────

function ModalShell({
  title, subtitle, onClose, children, wide = false,
}: {
  title: string
  subtitle: string
  onClose: () => void
  children: React.ReactNode
  wide?: boolean
}) {
  return (
    <Modal
      open
      onClose={onClose}
      // Backdrop closes, Escape did not: kept.
      closeOnBackdrop
      closeOnEscape={false}
      size="full"
      className={wide ? 'max-w-6xl' : 'max-w-md'}
      title={title}
      description={subtitle}
      // The matrix and forms keep their own scroll area and footer.
      bodyClassName="flex min-h-0 flex-col overflow-hidden p-0"
    >
      {children}
    </Modal>
  )
}

// ── Matrix table (the core grid) ─────────────────────────────────────────────

interface MatrixTableProps {
  rowFactor: ApiFactor
  colFactor: ApiFactor | null
  subColFactor: ApiFactor | null
  rowValues: ApiFactorValue[]
  colValues: ApiFactorValue[]
  subColValues: ApiFactorValue[]
  leafCols: LeafCol[]
  editable: boolean
  comboFor: (r: ApiFactorValue | null, c: ApiFactorValue | null, s: ApiFactorValue | null) => Record<string, string> | null
  cellValue: (combo: Record<string, string>) => number
  setCellValue: (combo: Record<string, string>, value: number) => void
  isCellModified: (combo: Record<string, string>) => boolean
  cellHasNote: (combo: Record<string, string>) => boolean
  rowTotal: (row: ApiFactorValue) => number
  colTotal: (col: ApiFactorValue) => number
  subColTotal: (col: ApiFactorValue, sub: ApiFactorValue) => number
  grandTotal: number
  focusedKey: CellKey | null
  setFocusedKey: (k: CellKey | null) => void
}

function MatrixTable(props: MatrixTableProps) {
  const {
    rowFactor, colFactor, subColFactor,
    rowValues, colValues, subColValues, leafCols,
    editable, comboFor, cellValue, setCellValue, isCellModified, cellHasNote,
    rowTotal, colTotal, subColTotal, grandTotal,
    focusedKey, setFocusedKey,
  } = props

  return (
    <Table density="compact" className="min-w-full">
      <THead className="sticky top-0 z-10">
        {/* Top header row (only when 3 factors → col group labels) */}
        {subColFactor && (
          <Tr>
            <Th className="sticky left-0 bg-surface-subtle border-b border-r border-line" />
            {colValues.map((cv) => (
              <Th align="center"
                key={cv.id}
                colSpan={subColValues.length + 1}
                className="border-b border-r border-line bg-surface-sunken"
              >
                {colFactor!.factor_name}: {cv.value_name}
                {!cv.is_active && <span className="ml-1 text-fg-muted line-through">·</span>}
              </Th>
            ))}
            <Th align="right" rowSpan={2} className="border-b border-line text-primary-700 bg-primary-50">
              Row Total
            </Th>
          </Tr>
        )}

        {/* Leaf header row */}
        <Tr>
          <Th className="sticky left-0 bg-surface-subtle border-b border-r border-line min-w-32">
            {rowFactor.factor_name}
          </Th>
          {leafCols.map((lc, i) => {
            if (lc.kind === 'amount') {
              return (
                <Th align="right" key={i} className="border-b border-r border-line min-w-24">
                  Amount
                </Th>
              )
            }
            if (lc.kind === 'col') {
              return (
                <Th
                  key={i}
                  className={cn(
                    'border-b border-r border-line px-2 py-2 text-right text-2xs font-semibold text-fg-secondary min-w-24',
                    !lc.col.is_active && 'text-fg-muted line-through',
                  )}
                >
                  {colFactor!.factor_name}: {lc.col.value_name}
                </Th>
              )
            }
            if (lc.kind === 'sub') {
              return (
                <Th
                  key={i}
                  className={cn(
                    'border-b border-r border-line px-2 py-2 text-right text-2xs font-semibold text-fg-secondary min-w-20',
                    !lc.sub.is_active && 'text-fg-muted line-through',
                  )}
                >
                  {lc.sub.value_name}
                </Th>
              )
            }
            return (
              <Th align="right" key={i} className="border-b border-r border-line bg-surface-sunken">
                Sum
              </Th>
            )
          })}
          {!subColFactor && (
            <Th align="right" className="border-b border-line text-primary-700 bg-primary-50">
              Row Total
            </Th>
          )}
        </Tr>
      </THead>

      <TBody>
        {rowValues.map((rv) => (
          <Tr key={rv.id} className={cn(!rv.is_active && 'opacity-50')}>
            <Td className={cn(
              'sticky left-0 bg-surface border-b border-r border-line px-3 py-1.5 text-left font-medium text-fg',
              !rv.is_active && 'line-through text-fg-muted',
            )}>
              {rv.value_name}
            </Td>
            {leafCols.map((lc, i) => {
              if (lc.kind === 'amount') {
                const combo = comboFor(rv, null, null)
                return (
                  <EditableCell
                    key={i}
                    combo={combo}
                    value={combo ? cellValue(combo) : 0}
                    modified={combo ? isCellModified(combo) : false}
                    hasNote={combo ? cellHasNote(combo) : false}
                    editable={editable && rv.is_active}
                    onChange={(v) => combo && setCellValue(combo, v)}
                    onFocus={() => combo && setFocusedKey(comboKey(combo))}
                  />
                )
              }
              if (lc.kind === 'col') {
                const combo = comboFor(rv, lc.col, null)
                return (
                  <EditableCell
                    key={i}
                    combo={combo}
                    value={combo ? cellValue(combo) : 0}
                    modified={combo ? isCellModified(combo) : false}
                    hasNote={combo ? cellHasNote(combo) : false}
                    editable={editable && rv.is_active && lc.col.is_active}
                    onChange={(v) => combo && setCellValue(combo, v)}
                    onFocus={() => combo && setFocusedKey(comboKey(combo))}
                  />
                )
              }
              if (lc.kind === 'sub') {
                const combo = comboFor(rv, lc.col, lc.sub)
                return (
                  <EditableCell
                    key={i}
                    combo={combo}
                    value={combo ? cellValue(combo) : 0}
                    modified={combo ? isCellModified(combo) : false}
                    hasNote={combo ? cellHasNote(combo) : false}
                    editable={editable && rv.is_active && lc.col.is_active && lc.sub.is_active}
                    onChange={(v) => combo && setCellValue(combo, v)}
                    onFocus={() => combo && setFocusedKey(comboKey(combo))}
                  />
                )
              }
              // col_sum: sum across sub-cols for this (row, col)
              const rowColSum = subColValues.reduce((s, sv) => {
                const c = comboFor(rv, lc.col, sv)
                return c ? s + cellValue(c) : s
              }, 0)
              return (
                <Td align="right" key={i} className="border-b border-r border-line font-mono text-xs bg-surface-subtle text-fg-secondary">
                  {formatAmount(rowColSum, 'CAD')}
                </Td>
              )
            })}
            {!subColFactor && (
              <Td align="right" className="border-b border-line font-mono text-xs bg-primary-50 text-primary-800 font-semibold">
                {formatAmount(rowTotal(rv), 'CAD')}
              </Td>
            )}
            {subColFactor && (
              <Td align="right" className="border-b border-line font-mono text-xs bg-primary-50 text-primary-800 font-semibold">
                {formatAmount(rowTotal(rv), 'CAD')}
              </Td>
            )}
          </Tr>
        ))}

        {/* Column total footer */}
        <Tr className="bg-surface-subtle">
          <Td className="sticky left-0 bg-surface-subtle border-t border-r border-line text-left text-2xs font-semibold text-fg-secondary">
            Total
          </Td>
          {leafCols.map((lc, i) => {
            if (lc.kind === 'amount') {
              const total = rowValues.reduce((s, rv) => s + rowTotal(rv), 0)
              return (
                <Td align="right" key={i} className="border-t border-r border-line font-mono text-xs bg-surface-sunken text-fg font-semibold">
                  {formatAmount(total, 'CAD')}
                </Td>
              )
            }
            if (lc.kind === 'col') {
              return (
                <Td align="right" key={i} className="border-t border-r border-line font-mono text-xs bg-surface-sunken text-fg font-semibold">
                  {formatAmount(colTotal(lc.col), 'CAD')}
                </Td>
              )
            }
            if (lc.kind === 'sub') {
              return (
                <Td align="right" key={i} className="border-t border-r border-line font-mono text-xs bg-surface-sunken text-fg font-semibold">
                  {formatAmount(subColTotal(lc.col, lc.sub), 'CAD')}
                </Td>
              )
            }
            return (
              <Td align="right" key={i} className="border-t border-r border-line font-mono text-xs bg-surface-strong text-fg font-semibold">
                {formatAmount(colTotal(lc.col), 'CAD')}
              </Td>
            )
          })}
          <Td align="right" className="border-t border-line font-mono text-xs bg-primary-100 text-primary-900 font-semibold">
            {formatAmount(grandTotal, 'CAD')}
          </Td>
        </Tr>
      </TBody>
    </Table>
  )
}

// ── Editable cell ────────────────────────────────────────────────────────────

function EditableCell({
  value, modified, hasNote, editable, onChange, onFocus,
}: {
  combo: Record<string, string> | null
  value: number
  modified: boolean
  hasNote: boolean
  editable: boolean
  onChange: (v: number) => void
  onFocus: () => void
}) {
  const [local, setLocal] = useState(value === 0 ? '' : String(value))

  // Sync external value changes (e.g. after Spread / Reset / Undo / Paste).
  useEffect(() => {
    setLocal(value === 0 ? '' : String(value))
  }, [value])

  // Tiny amber triangle in the top-right corner — Excel's "comment indicator" idiom.
  const noteIndicator = hasNote ? (
    <span
      aria-hidden
      title="Has note — see Reference panel"
      className="pointer-events-none absolute top-0 right-0 h-0 w-0 border-t-[6px] border-l-[6px] border-l-transparent border-t-warning-solid"
    />
  ) : null

  if (!editable) {
    return (
      <Td align="right" className="relative border-b border-r border-line font-mono text-xs text-fg-muted bg-surface-subtle">
        {noteIndicator}
        {value > 0 ? formatAmount(value, 'CAD') : '—'}
      </Td>
    )
  }

  return (
    <Td
      className={cn(
        'relative border-b border-r border-line p-0',
        modified ? 'bg-warning-soft' : 'bg-surface',
      )}
    >
      {noteIndicator}
      <Input bare
        type="number"
        step="0.01"
        min="0"
        value={local}
        onFocus={onFocus}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={() => {
          const n = parseFloat(local || '0')
          onChange(Number.isFinite(n) && n >= 0 ? n : 0)
        }}
        placeholder="0"
        className="w-full text-right font-mono text-xs px-2 py-1.5 border-0 focus:ring-1 focus:ring-primary-500 focus:bg-surface"
      />
    </Td>
  )
}

// ── Spread menu ──────────────────────────────────────────────────────────────

function SpreadMenu({
  anchorRect, onClose, onApply, hasFocus, hasColAxis,
}: {
  anchorRect: DOMRect
  onClose: () => void
  onApply: (scope: 'row' | 'col' | 'all', method: 'evenly' | 'proportional' | 'fill', target: number) => void
  hasFocus: boolean
  hasColAxis: boolean
}) {
  const [scope, setScope] = useState<'row' | 'col' | 'all'>('row')
  const [method, setMethod] = useState<'evenly' | 'proportional' | 'fill'>('evenly')
  const [value, setValue] = useState('')

  const submit = () => {
    const n = parseFloat(value || '0')
    if (!Number.isFinite(n) || n < 0) return
    onApply(scope, method, n)
  }

  return (
    // Portalled above the matrix dialog (z-50) so an outside click inside it closes the menu.
    <DropdownPortal anchorRect={anchorRect} onClose={onClose} align="end" width={288}
      className="flex max-h-none flex-col gap-2.5 overflow-visible p-3">
        <div className="flex flex-col gap-1">
          <label className="text-2xs text-fg-muted font-semibold">Scope</label>
          <div className="grid grid-cols-3 gap-1">
            <ScopeBtn active={scope === 'row'} disabled={!hasFocus} onClick={() => setScope('row')}>Row</ScopeBtn>
            <ScopeBtn active={scope === 'col'} disabled={!hasFocus || !hasColAxis} onClick={() => setScope('col')}>Column</ScopeBtn>
            <ScopeBtn active={scope === 'all'} onClick={() => setScope('all')}>All</ScopeBtn>
          </div>
          {(scope === 'row' || scope === 'col') && !hasFocus && (
            <p className="text-2xs text-fg-muted">Click a cell first to anchor row/column.</p>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-2xs text-fg-muted font-semibold">Method</label>
          <div className="grid grid-cols-3 gap-1">
            <ScopeBtn active={method === 'evenly'} onClick={() => setMethod('evenly')}>Evenly</ScopeBtn>
            <ScopeBtn active={method === 'proportional'} onClick={() => setMethod('proportional')}>Proportional</ScopeBtn>
            <ScopeBtn active={method === 'fill'} onClick={() => setMethod('fill')}>Fill</ScopeBtn>
          </div>
          <p className="text-2xs text-fg-muted leading-snug">
            {method === 'evenly' && 'Equal value in every target cell. Total = entered value.'}
            {method === 'proportional' && 'Weighted by current cell values. Total = entered value.'}
            {method === 'fill' && 'Set every target cell to the entered value.'}
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-2xs text-fg-muted font-semibold">
            {method === 'fill' ? 'Value per cell' : 'Target total'}
          </label>
          <Input inputSize="sm"
            type="number" step="0.01" min="0"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="0.00"
            className="font-mono"
            autoFocus
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
          />
        </div>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={!value || ((scope !== 'all') && !hasFocus)}>Apply</Button>
        </div>
    </DropdownPortal>
  )
}

function ScopeBtn({
  active, disabled, onClick, children,
}: {
  active: boolean
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Button
      size="xs"
      variant={active ? 'primary' : 'subtle'}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={cn('w-full px-1 text-2xs', !active && 'bg-surface-sunken hover:bg-surface-strong')}
    >
      {children}
    </Button>
  )
}

// ── Reference sidebar ────────────────────────────────────────────────────────

function BaselineSidebar({
  baseline, focusedKey, cellValue, cellNote, onChangeNote, editable, grandTotal,
}: {
  baseline: {
    last_year_same_month: number | null
    current_month_actual: number | null
    ytd_actual: number | null
    last_year_breakdowns: ApiBreakdown[]
  } | undefined
  focusedKey: CellKey | null
  cellValue: (combo: Record<string, string>) => number
  cellNote: (combo: Record<string, string>) => string
  onChangeNote: (combo: Record<string, string>, note: string) => void
  editable: boolean
  grandTotal: number
}) {
  const lastYear = baseline?.last_year_same_month != null ? Number(baseline.last_year_same_month) : null
  const yoyDelta = lastYear != null && lastYear > 0 ? ((grandTotal - lastYear) / lastYear) * 100 : null
  const actual = baseline?.current_month_actual != null ? Number(baseline.current_month_actual) : null
  const ytd = baseline?.ytd_actual != null ? Number(baseline.ytd_actual) : null

  let selectedCombo: Record<string, string> | null = null
  let selectedValue = 0
  let selectedNote = ''
  if (focusedKey) {
    try {
      selectedCombo = JSON.parse(focusedKey)
      if (selectedCombo) {
        selectedValue = cellValue(selectedCombo)
        selectedNote = cellNote(selectedCombo)
      }
    } catch { /* ignore */ }
  }

  // Local draft so typing doesn't push a snapshot per keystroke — commit on blur.
  const [noteDraft, setNoteDraft] = useState(selectedNote)
  useEffect(() => { setNoteDraft(selectedNote) }, [selectedNote, focusedKey])

  return (
    <aside className="w-64 shrink-0 border-l border-line bg-surface-subtle/60 overflow-y-auto">
      <div className="p-4 flex flex-col gap-4 text-xs">
        <div>
          <div className="flex items-center gap-1.5 text-2xs font-semibold text-fg-muted mb-2">
            <Info className="h-3 w-3" /> Reference
          </div>
        </div>

        <Section label="Last year · same month">
          {lastYear != null ? (
            <>
              <p className="font-mono text-sm font-semibold text-fg">{formatAmount(lastYear, 'CAD')}</p>
              {yoyDelta != null && (
                <p className={cn(
                  'text-2xs mt-0.5',
                  yoyDelta > 0 ? 'text-success-fg' : yoyDelta < 0 ? 'text-danger-fg' : 'text-fg-muted',
                )}>
                  {yoyDelta >= 0 ? '+' : ''}{yoyDelta.toFixed(1)}% vs current total
                </p>
              )}
            </>
          ) : <p className="text-fg-muted">—</p>}
        </Section>

        <Section label="Actual · this month">
          <p className="font-mono text-sm text-fg-secondary">
            {actual != null ? formatAmount(actual, 'CAD') : '—'}
          </p>
        </Section>

        <Section label="Actual · YTD">
          <p className="font-mono text-sm text-fg-secondary">
            {ytd != null ? formatAmount(ytd, 'CAD') : '—'}
          </p>
        </Section>

        <Section label="Selected cell">
          {selectedCombo ? (
            <>
              <p className="text-2xs text-fg-secondary">
                {Object.entries(selectedCombo).map(([k, v]) => `${k}=${v}`).join(' × ')}
              </p>
              <p className="font-mono text-sm font-semibold text-fg mt-1">
                {formatAmount(selectedValue, 'CAD')}
              </p>
              <div className="mt-2">
                <label className="text-2xs text-fg-muted font-medium flex items-center gap-1 mb-1">
                  <MessageSquare className="h-3 w-3" /> Note
                </label>
                {editable ? (
                  <Textarea
                    value={noteDraft}
                    onChange={(e) => setNoteDraft(e.target.value)}
                    onBlur={() => {
                      if (selectedCombo && noteDraft !== selectedNote) {
                        onChangeNote(selectedCombo, noteDraft)
                      }
                    }}
                    placeholder="Add a note for this cell…"
                    rows={3}
                    className="w-full resize-y"
                  />
                ) : (
                  <p className="text-2xs text-fg-secondary whitespace-pre-wrap">
                    {selectedNote || <span className="text-fg-muted">—</span>}
                  </p>
                )}
              </div>
            </>
          ) : <p className="text-fg-muted">Click a cell</p>}
        </Section>

        <Section label="Matrix total">
          <p className="font-mono text-sm font-semibold text-primary-800">
            {formatAmount(grandTotal, 'CAD')}
          </p>
          <p className="text-2xs text-fg-muted mt-0.5">
            Saved to plan_line.amount on Save.
          </p>
        </Section>
      </div>
    </aside>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-2xs text-fg-muted font-medium mb-1">{label}</p>
      {children}
    </div>
  )
}
