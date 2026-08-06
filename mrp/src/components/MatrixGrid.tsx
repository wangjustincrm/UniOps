/**
 * MatrixGrid — editable row x column numeric grid with Excel paste and
 * undo/redo. Built for the Sales Forecast page (product x month) but kept
 * generic (rows/cols are just {id,label}) so other MRP pages can reuse it.
 *
 * Ported from epms/src/pages/budget/BreakdownMatrixModal.tsx:
 *   - undo/redo snapshot stack -> matrixGrid/history.ts (lines 116-151 there)
 *   - rectangular Excel paste  -> matrixGrid/pasteLogic.ts (lines 327-370 there)
 * Both are pure, framework-free modules here so they're independently
 * verifiable (see matrixGrid/verify.ts) — this file is the React wiring:
 * keyboard nav, selection, rendering, and row virtualization.
 *
 * State shape:
 *   - `value` (prop) seeds the internal undo history on mount. This is a
 *     controlled-ish / uncontrolled-ish hybrid, same trade-off the
 *     blueprint makes: undo/redo needs its own linear history, so the
 *     component owns it once mounted. To load a *different* dataset
 *     (e.g. switch forecast version), remount with a different `key` —
 *     do not expect `value` prop changes to reset history in place.
 *   - Every commit (edit, paste, undo, redo, reset) calls `onChange` with
 *     the new sparse Map so the parent can hold it for Save.
 */
import {
  useCallback, useEffect, useId, useMemo, useRef, useState,
  type ReactNode, type MouseEvent as ReactMouseEvent,
} from 'react'
import { Undo2, Redo2, Lock, AlertTriangle, History } from 'lucide-react'
import { Button, Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import {
  initHistory, commitCells, commitCellsAndRows, undo as undoHistory, redo as redoHistory,
  currentCells, originalCells, currentExtraRows, canUndo as historyCanUndo, canRedo as historyCanRedo,
  type CellValueMap,
} from './matrixGrid/history'
import {
  cellKey, isMultiCellPaste, planPaste, applyPasteUpdates, formatPasteReport,
  isFullTablePaste, planFullTablePaste, formatFullTablePasteReport,
  selectionToTsv, normalizeRange,
  type GridRow, type GridCol, type PastePlan, type FullTablePastePlan,
  type PasteAnchor, type RangeSelection, type MaterialResolver,
} from './matrixGrid/pasteLogic'
import { ConfirmDialog } from './ConfirmDialog'

export type { GridRow, GridCol }

const ROW_HEIGHT = 32
const OVERSCAN = 8
const CONFIRM_THRESHOLD = 100

/** A paste plan awaiting the >100-cell confirmation dialog — either shape (see the paste handler below). */
type PendingPastePlan =
  | { kind: 'rect'; plan: PastePlan }
  | { kind: 'fullTable'; plan: FullTablePastePlan }

export interface MatrixGridProps {
  rows: GridRow[]
  cols: GridCol[]
  /** Sparse map: only cells with a non-zero value are present. Key = `${rowId}::${colId}`. */
  value: CellValueMap
  onChange: (next: CellValueMap) => void
  /** Cell keys (see cellKey()) that cannot be edited or pasted into. */
  frozenKeys?: ReadonlySet<string>
  readOnly?: boolean
  /** Fixed pixel height for the scrollable body — required for virtualization to do anything. */
  height?: number
  /** Label for the sticky first column header (e.g. "Product"). */
  rowHeaderLabel?: string
  /** Label for the trailing row-total column (e.g. "Total (kg)"). */
  rowTotalLabel?: string
  /** Label for the trailing column-total row (e.g. "Monthly Sum"). */
  colTotalLabel?: string
  /** Optional number formatter for display (defaults to en-US grouping, 0 decimals). */
  formatValue?: (n: number) => string
  /**
   * Imperatively focus one cell — set by the parent right after it appends a
   * new row (e.g. ForecastPage's "Add Product") so the planner can start
   * typing immediately, same as any keyboard-driven navigation (scrolls the
   * row into view too if needed). `colId` defaults to the first column.
   * Cleared via `onFocusRequestHandled` once applied, so re-requesting the
   * same cell again later still fires.
   */
  focusRequest?: { rowId: string; colId?: string } | null
  onFocusRequestHandled?: () => void
  /**
   * Row id whose cell values (all columns) should be purged from history —
   * used when the parent removes a not-yet-saved row, so stray edits don't
   * silently resurrect if the same row id is added back later. A no-op if
   * the row currently has no values. Cleared via `onRowCleared` once applied.
   */
  clearRowId?: string | null
  onRowCleared?: () => void
  /**
   * Optional extra content rendered at the right edge of a row's sticky
   * header cell (e.g. a remove control for a not-yet-saved row). Never
   * shown while `readOnly`.
   */
  rowActions?: (row: GridRow) => ReactNode
  /**
   * Looks up a pasted product code against known materials. When supplied,
   * a multi-cell paste whose first column resolves through this (rather
   * than parsing as a number) is planned as a row-creating "full-table"
   * paste instead of the rectangular numeric-block paste — see
   * matrixGrid/pasteLogic.ts's isFullTablePaste/planFullTablePaste. Omit to
   * keep this grid purely numeric (no product-code concept), which is what
   * every other MatrixGrid caller besides ForecastPage wants.
   */
  resolveMaterial?: MaterialResolver
  /**
   * Column ids to visually mark in the header (e.g. Sales Forecast's
   * `[current_month, current_month+18)` outlook window) — purely cosmetic,
   * no effect on editability/paste/frozen behavior.
   */
  highlightColIds?: ReadonlySet<string>
  /**
   * When supplied, every cell (editable or frozen/read-only) renders a tiny
   * history affordance in its corner; clicking it calls this instead of
   * selecting the cell (click is stopped from bubbling to the cell's own
   * onSelect). `anchorEl` is the clicked button, for a caller-owned portal
   * popover to position itself against (e.g. Sales Forecast's
   * CellHistoryPopover). Omit to render no affordance at all.
   */
  onCellHistoryClick?: (rowId: string, colId: string, anchorEl: HTMLElement) => void
}

function defaultFormat(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
}

interface FocusCell {
  rowIdx: number
  colIdx: number
}

export function MatrixGrid({
  rows: rowsProp, cols, value, onChange, frozenKeys, readOnly = false, height = 480,
  rowHeaderLabel = 'Row', rowTotalLabel = 'Total', colTotalLabel = 'Total',
  formatValue = defaultFormat,
  focusRequest = null, onFocusRequestHandled, clearRowId = null, onRowCleared, rowActions,
  resolveMaterial, highlightColIds, onCellHistoryClick,
}: MatrixGridProps) {
  const frozen = frozenKeys ?? EMPTY_FROZEN
  const [history, setHistory] = useState(() => initHistory(new Map(value)))
  const cells = currentCells(history)
  const original = originalCells(history)
  const canUndo = historyCanUndo(history)
  const canRedo = historyCanRedo(history)
  // Rows a full-table paste created this session, merged onto the caller's
  // `rows` prop for rendering/nav/totals purposes. They live in `history`
  // (see history.ts's Snapshot.extraRows) rather than component state so
  // undo/redo reverts a paste's new rows together with its cell values —
  // one paste, one undo step, per the design spec. Deduped against
  // `rowsProp` so a row that lands for real (e.g. after Save Draft's
  // refetch remounts this component with a fresh `key`) isn't shown twice
  // for the one render before remount actually happens.
  const pasteExtraRows = currentExtraRows(history)
  const rows = useMemo(() => {
    if (pasteExtraRows.length === 0) return rowsProp
    const known = new Set(rowsProp.map((r) => r.id))
    const extra = pasteExtraRows.filter((r) => !known.has(r.id))
    return extra.length === 0 ? rowsProp : [...rowsProp, ...extra]
  }, [rowsProp, pasteExtraRows])

  const [focus, setFocus] = useState<FocusCell | null>(null)
  const [selEnd, setSelEnd] = useState<FocusCell | null>(null)
  const [invalidByKey, setInvalidByKey] = useState<Map<string, string>>(new Map())
  const [report, setReport] = useState<string | null>(null)
  // A pending >100-cell paste confirmation (see the paste handler below).
  // GATING CHOICE (documented here, not just at each call site): while this
  // is non-null the whole grid is locked — no cell edits commit and no
  // further paste is intercepted — rather than the alternative of silently
  // replacing/invalidating the pending plan with whatever the user does
  // next. Replacing-on-new-action was rejected because it lets a second,
  // smaller paste commit *underneath* a confirmation dialog that still
  // describes the first (now-discarded) plan — the user would confirm
  // something that no longer matches what's on screen. Locking means the
  // dialog's summary is always an accurate description of what Confirm
  // will do; the user must Confirm or Cancel it before touching anything
  // else. `locked` below is the single source of truth for this gate.
  const [confirmPlan, setConfirmPlan] = useState<PendingPastePlan | null>(null)
  const locked = confirmPlan !== null

  // Emit changes to the parent on every committed history step.
  const lastEmitted = useRef(cells)
  useEffect(() => {
    if (lastEmitted.current !== cells) {
      lastEmitted.current = cells
      onChange(cells)
    }
  }, [cells, onChange])

  const applyUpdater = useCallback((updater: (prev: CellValueMap) => CellValueMap) => {
    setHistory((h) => commitCells(h, updater))
  }, [])

  const setCellValue = useCallback((rowIdx: number, colIdx: number, v: number) => {
    const key = cellKey(rows[rowIdx].id, cols[colIdx].id)
    if (frozen.has(key)) return
    // Belt-and-suspenders: the <input> is also rendered `disabled` while
    // `locked` (see MatrixCell), which normally prevents this from firing
    // at all. But disabling a focused input forces a native blur first,
    // which would otherwise commit whatever was mid-edit at the moment the
    // lock engaged — guard here too so that edit never lands.
    if (locked) return
    applyUpdater((prev) => {
      const next = new Map(prev)
      if (v === 0) next.delete(key)
      else next.set(key, v)
      return next
    })
    setInvalidByKey((m) => {
      if (!m.has(key)) return m
      const n = new Map(m)
      n.delete(key)
      return n
    })
  }, [rows, cols, frozen, applyUpdater, locked])

  // ── Selection helpers ────────────────────────────────────────────────────

  const currentRange = useCallback((): RangeSelection | null => {
    if (!focus) return null
    const end = selEnd ?? focus
    return { startRow: focus.rowIdx, startCol: focus.colIdx, endRow: end.rowIdx, endCol: end.colIdx }
  }, [focus, selEnd])

  const isInSelection = useCallback((rowIdx: number, colIdx: number): boolean => {
    const r = currentRange()
    if (!r) return false
    const n = normalizeRange(r)
    return rowIdx >= n.startRow && rowIdx <= n.endRow && colIdx >= n.startCol && colIdx <= n.endCol
  }, [currentRange])

  // Set only for keyboard-driven moves (Tab/Arrow/Enter) — tells the
  // focus-sync effect below it's allowed to force the viewport to scroll to
  // reveal this cell. Mouse clicks never set this: the clicked cell is
  // already visible (that's how it got clicked) and already has real DOM
  // focus, so no scroll-forcing is needed — and forcing it unconditionally
  // on every scrollTop change would fight the user the moment they scroll
  // the grid away from whatever cell last had focus.
  const pendingNavKey = useRef<string | null>(null)

  const focusCell = useCallback((rowIdx: number, colIdx: number, extend = false, viaKeyboard = false) => {
    if (rowIdx < 0 || rowIdx >= rows.length || colIdx < 0 || colIdx >= cols.length) return
    if (extend) {
      setSelEnd({ rowIdx, colIdx })
    } else {
      if (viaKeyboard) pendingNavKey.current = cellKey(rows[rowIdx].id, cols[colIdx].id)
      setFocus({ rowIdx, colIdx })
      setSelEnd(null)
    }
  }, [rows, cols])

  // ── Imperative focus / row-clear requests from the parent ───────────────
  // (see the props' doc comments above — used by ForecastPage's "Add
  // Product" and "remove a not-yet-saved row" flows.)
  useEffect(() => {
    if (!focusRequest) return
    const rowIdx = rows.findIndex((r) => r.id === focusRequest.rowId)
    if (rowIdx === -1) return // row not in the current `rows` prop yet — nothing to focus
    const colIdx = focusRequest.colId ? cols.findIndex((c) => c.id === focusRequest.colId) : 0
    if (colIdx === -1) return
    focusCell(rowIdx, colIdx, false, true)
    onFocusRequestHandled?.()
  }, [focusRequest, rows, cols, focusCell, onFocusRequestHandled])

  useEffect(() => {
    if (!clearRowId) return
    applyUpdater((prev) => {
      let changed = false
      const next = new Map(prev)
      for (const c of cols) {
        const k = cellKey(clearRowId, c.id)
        if (next.has(k)) { next.delete(k); changed = true }
      }
      return changed ? next : prev
    })
    setFocus((f) => (f && rows[f.rowIdx]?.id === clearRowId ? null : f))
    setSelEnd((s) => (s && rows[s.rowIdx]?.id === clearRowId ? null : s))
    onRowCleared?.()
  }, [clearRowId, cols, rows, applyUpdater, onRowCleared])

  // ── Apply a paste plan (shared by direct-apply and confirm-then-apply) ──

  const applyRectPlan = useCallback((plan: PastePlan) => {
    if (plan.updates.length > 0) {
      applyUpdater((prev) => applyPasteUpdates(prev, plan.updates))
    }
    if (plan.invalidCells.length > 0) {
      setInvalidByKey((prevMap) => {
        const next = new Map(prevMap)
        for (const ic of plan.invalidCells) {
          next.set(cellKey(rows[ic.rowIdx].id, cols[ic.colIdx].id), ic.raw)
        }
        return next
      })
    }
    setReport(formatPasteReport(plan))
  }, [applyUpdater, rows, cols])

  // Row-creating paste: commits the new rows AND their cell values as one
  // history snapshot (commitCellsAndRows, not commitCells) — that's what
  // makes rows-created-plus-values a single undo step, per the design spec.
  const applyFullTablePlan = useCallback((plan: FullTablePastePlan) => {
    setHistory((h) => commitCellsAndRows(h, (prev) => {
      const cells = plan.updates.length > 0 ? applyPasteUpdates(prev.cells, plan.updates) : prev.cells
      if (plan.newRows.length === 0) return { cells, extraRows: prev.extraRows }
      const known = new Set([...rows.map((r) => r.id), ...prev.extraRows.map((r) => r.id)])
      const toAdd = plan.newRows.filter((r) => !known.has(r.id))
      const extraRows = toAdd.length === 0 ? prev.extraRows : [...prev.extraRows, ...toAdd]
      return { cells, extraRows }
    }))
    if (plan.invalidCells.length > 0) {
      setInvalidByKey((prevMap) => {
        const next = new Map(prevMap)
        for (const ic of plan.invalidCells) next.set(ic.key, ic.raw)
        return next
      })
    }
    setReport(formatFullTablePasteReport(plan))
  }, [rows])

  // ── Excel paste — window-level listener ──────────────────────────────
  // Mirrors epms BreakdownMatrixModal.tsx lines 327-370: only intercept
  // multi-cell pastes (tab or newline present); single-cell paste falls
  // through to the native input so default browser behaviour is kept.
  //
  // Two shapes, checked in this order:
  //  1. Full-table (row-creating): first column resolves to a known
  //     material via `resolveMaterial` rather than parsing as a number.
  //     Needs no focused cell — it can create rows from scratch on an
  //     empty grid, which is the whole point (see pasteLogic.ts header).
  //  2. Rectangular numeric block anchored at the focused cell (today's
  //     behaviour) — requires `focus`, unchanged from before.
  useEffect(() => {
    if (readOnly) return
    const handler = (e: ClipboardEvent) => {
      // A previous large paste is still awaiting Confirm/Cancel — ignore
      // this paste entirely rather than planning and applying it under the
      // dialog. See the `locked`/`confirmPlan` comment above for why
      // "block" was chosen over "replace the pending plan".
      if (locked) return
      const text = e.clipboardData?.getData('text/plain') ?? ''
      if (!isMultiCellPaste(text)) return

      if (resolveMaterial && isFullTablePaste(text, resolveMaterial)) {
        e.preventDefault()
        const existingRowIds = new Set(rows.map((r) => r.id))
        const plan = planFullTablePaste(text, cols, resolveMaterial, existingRowIds, frozen)
        const nothingToDo = plan.updates.length === 0 && plan.newRows.length === 0
          && plan.skippedRows === 0 && plan.skippedFrozen === 0 && plan.invalidCells.length === 0
        if (nothingToDo) return
        if (plan.totalCells > CONFIRM_THRESHOLD) {
          setConfirmPlan({ kind: 'fullTable', plan })
        } else {
          applyFullTablePlan(plan)
        }
        return
      }

      if (!focus) return
      e.preventDefault()
      const anchor: PasteAnchor = { rowIdx: focus.rowIdx, colIdx: focus.colIdx }
      const plan = planPaste(text, anchor, rows, cols, frozen)
      if (plan.updates.length === 0 && plan.skippedFrozen === 0 && plan.invalidCells.length === 0) return
      if (plan.totalCells > CONFIRM_THRESHOLD) {
        setConfirmPlan({ kind: 'rect', plan })
      } else {
        applyRectPlan(plan)
      }
    }
    window.addEventListener('paste', handler)
    return () => window.removeEventListener('paste', handler)
  }, [readOnly, focus, rows, cols, frozen, resolveMaterial, applyRectPlan, applyFullTablePlan, locked])

  // ── Copy — Ctrl+C emits TSV for the current selection ───────────────────
  const copySelection = useCallback(() => {
    const r = currentRange()
    if (!r) return
    const tsv = selectionToTsv(rows, cols, cells, r)
    navigator.clipboard?.writeText(tsv).catch(() => { /* clipboard permission denied — no-op */ })
  }, [currentRange, rows, cols, cells])

  // ── Keyboard: undo/redo, copy, arrow/tab/enter navigation ───────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey
      if (!mod) return
      const key = e.key.toLowerCase()
      if (key === 'z' && !e.shiftKey) {
        // Undo/redo mutate history — blocked while a paste confirmation is
        // open for the same reason cell edits are (see the `locked`
        // comment above): the confirmation's summary must stay an
        // accurate description of what Confirm will apply.
        if (readOnly || locked) return
        e.preventDefault()
        setHistory((h) => undoHistory(h))
      } else if (key === 'y' || (key === 'z' && e.shiftKey)) {
        if (readOnly || locked) return
        e.preventDefault()
        setHistory((h) => redoHistory(h))
      } else if (key === 'c') {
        // Every grid cell (editable input or frozen td) carries
        // data-matrix-input="true" (see MatrixCell) — only intercept Ctrl+C
        // when focus is actually inside this grid, so Ctrl+C on some other
        // input elsewhere on the page keeps native text-selection copy.
        // Copy is read-only, so it stays allowed even while `locked`.
        const target = e.target as HTMLElement | null
        if (!target?.closest('[data-matrix-input="true"]')) return
        e.preventDefault()
        copySelection()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [readOnly, copySelection, locked])

  const isCellModified = useCallback((rowIdx: number, colIdx: number): boolean => {
    const key = cellKey(rows[rowIdx].id, cols[colIdx].id)
    return (cells.get(key) ?? 0) !== (original.get(key) ?? 0)
  }, [rows, cols, cells, original])

  // ── Totals ────────────────────────────────────────────────────────────
  const rowTotal = useCallback((rowIdx: number): number => {
    let sum = 0
    const rowId = rows[rowIdx].id
    for (const c of cols) sum += cells.get(cellKey(rowId, c.id)) ?? 0
    return sum
  }, [rows, cols, cells])

  const colTotal = useCallback((colIdx: number): number => {
    let sum = 0
    const colId = cols[colIdx].id
    for (const r of rows) sum += cells.get(cellKey(r.id, colId)) ?? 0
    return sum
  }, [rows, cols, cells])

  const grandTotal = useMemo(() => {
    let sum = 0
    for (const v of cells.values()) sum += v
    return sum
  }, [cells])

  // ── Virtualization ───────────────────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const visibleCount = Math.ceil(height / ROW_HEIGHT) + OVERSCAN * 2
  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
  const endIdx = Math.min(rows.length, startIdx + visibleCount)
  const topSpacer = startIdx * ROW_HEIGHT
  const bottomSpacer = Math.max(0, (rows.length - endIdx) * ROW_HEIGHT)

  const onScroll = useCallback(() => {
    if (scrollRef.current) setScrollTop(scrollRef.current.scrollTop)
  }, [])

  // ── Keyboard-nav focus sync ──────────────────────────────────────────
  // Tab/Arrow/Enter navigation moves `focus` state (see focusCell) but the
  // key handlers preventDefault the native browser focus change (so our
  // own Tab handling wins over the browser's default tab order) — so
  // nothing else would ever move real DOM focus to the new cell's <input>.
  // This effect is that missing piece, gated by `pendingNavKey` so it only
  // acts on keyboard-driven moves — see the comment on pendingNavKey above
  // for why: an ungated version fights the user any time they scroll the
  // grid while a cell elsewhere remains focused.
  const cellRefs = useRef(new Map<string, HTMLElement>())
  const registerCellRef = useCallback((key: string) => (el: HTMLElement | null) => {
    if (el) cellRefs.current.set(key, el)
    else cellRefs.current.delete(key)
  }, [])
  useEffect(() => {
    if (!focus) { pendingNavKey.current = null; return }
    const key = cellKey(rows[focus.rowIdx].id, cols[focus.colIdx].id)
    const el = cellRefs.current.get(key)
    if (el) {
      if (pendingNavKey.current === key) {
        if (document.activeElement !== el) el.focus()
        pendingNavKey.current = null
      }
      return
    }
    if (pendingNavKey.current !== key) return // not an in-flight keyboard move — leave scroll position alone
    const scrollEl = scrollRef.current
    if (!scrollEl) return
    const rowTop = focus.rowIdx * ROW_HEIGHT
    const rowBottom = rowTop + ROW_HEIGHT
    if (rowTop < scrollEl.scrollTop) scrollEl.scrollTop = rowTop
    else if (rowBottom > scrollEl.scrollTop + scrollEl.clientHeight) scrollEl.scrollTop = rowBottom - scrollEl.clientHeight
    // `scrollTop` is a deliberate re-run trigger (not just a data dependency):
    // the manual scrollTop assignment above fires a native scroll event,
    // onScroll updates scrollTop state, and that state change re-runs this
    // effect so the now-rendered cell can be found in cellRefs and focused
    // (still gated by pendingNavKey, so it stops as soon as the target is
    // reached — it won't keep re-forcing position on later, unrelated scrolls).
  }, [focus, rows, cols, scrollTop])

  const gridId = useId()

  return (
    <div className="flex flex-col gap-2">
      {!readOnly && (
        <div className="flex items-center gap-2">
          <Button
            type="button" size="icon-sm" variant="secondary"
            disabled={!canUndo || locked}
            onClick={() => setHistory((h) => undoHistory(h))}
            title="Undo (Ctrl+Z)"
            aria-label="Undo"
          >
            <Undo2 className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button" size="icon-sm" variant="secondary"
            disabled={!canRedo || locked}
            onClick={() => setHistory((h) => redoHistory(h))}
            title="Redo (Ctrl+Y)"
            aria-label="Redo"
          >
            <Redo2 className="h-3.5 w-3.5" />
          </Button>
          <span className="text-xs text-neutral-400">
            Click a cell, Tab/arrows to move, Enter moves down · paste a block from Excel
            {resolveMaterial && ', or paste a full table (product code, optional name, month values) to add new product rows — even on an empty grid'}
            {' '}· Ctrl+Z/Y undo/redo · select a range + Ctrl+C to copy
          </span>
        </div>
      )}

      {report && (
        <div
          role="alert"
          className="rounded-md border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs text-primary-800 flex items-center justify-between"
        >
          <span>{report}</span>
          <button
            type="button"
            onClick={() => setReport(null)}
            className="text-primary-600 hover:text-primary-800 font-medium ml-3"
          >
            Dismiss
          </button>
        </div>
      )}

      {confirmPlan && confirmPlan.kind === 'rect' && (
        // Reuses the shared ConfirmDialog (portal + backdrop) instead of the
        // inline banner this used to be — the backdrop is also what makes
        // the "gate mouse interaction with the grid" half of `locked` work
        // for free; the disabled-input / effect-level checks above handle
        // the keyboard half a backdrop alone can't cover.
        <ConfirmDialog
          title="Large paste — confirm before applying"
          confirmLabel={`Apply ${confirmPlan.plan.updates.length} cell${confirmPlan.plan.updates.length === 1 ? '' : 's'}`}
          onCancel={() => setConfirmPlan(null)}
          onConfirm={() => {
            applyRectPlan(confirmPlan.plan)
            setConfirmPlan(null)
          }}
        >
          <p className="flex items-start gap-1.5 text-warning-800">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>
              This paste affects <strong>{confirmPlan.plan.affectedRows}</strong> row{confirmPlan.plan.affectedRows === 1 ? '' : 's'} x{' '}
              <strong>{confirmPlan.plan.affectedCols}</strong> column{confirmPlan.plan.affectedCols === 1 ? '' : 's'} ={' '}
              <strong>{confirmPlan.plan.totalCells}</strong> cells.
              {confirmPlan.plan.skippedFrozen > 0 && <> {confirmPlan.plan.skippedFrozen} of those are frozen and will be skipped.</>}
              {confirmPlan.plan.invalidCells.length > 0 && <> {confirmPlan.plan.invalidCells.length} cell(s) are not valid numbers and will be flagged, not applied.</>}
            </span>
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            {confirmPlan.plan.skippedFrozen > 0 && <Badge variant="neutral">{confirmPlan.plan.skippedFrozen} frozen skipped</Badge>}
            {confirmPlan.plan.invalidCells.length > 0 && <Badge variant="danger">{confirmPlan.plan.invalidCells.length} invalid</Badge>}
            {(confirmPlan.plan.clippedRows || confirmPlan.plan.clippedCols) && <Badge variant="warning">clipped to grid bounds</Badge>}
          </div>
        </ConfirmDialog>
      )}

      {confirmPlan && confirmPlan.kind === 'fullTable' && (
        <ConfirmDialog
          title="Large paste — confirm before applying"
          confirmLabel={`Apply ${confirmPlan.plan.updates.length} cell${confirmPlan.plan.updates.length === 1 ? '' : 's'}${confirmPlan.plan.newRows.length > 0 ? ` (${confirmPlan.plan.newRows.length} new row${confirmPlan.plan.newRows.length === 1 ? '' : 's'})` : ''}`}
          onCancel={() => setConfirmPlan(null)}
          onConfirm={() => {
            applyFullTablePlan(confirmPlan.plan)
            setConfirmPlan(null)
          }}
        >
          <p className="flex items-start gap-1.5 text-warning-800">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>
              This paste affects <strong>{confirmPlan.plan.totalRows}</strong> product row{confirmPlan.plan.totalRows === 1 ? '' : 's'}
              {' '}(<strong>{confirmPlan.plan.newRows.length}</strong> new) across <strong>{confirmPlan.plan.updates.length}</strong> cells.
              {confirmPlan.plan.skippedRows > 0 && <> {confirmPlan.plan.skippedRows} row(s) will be skipped — unknown product code.</>}
              {confirmPlan.plan.skippedFrozen > 0 && <> {confirmPlan.plan.skippedFrozen} cell(s) are frozen and will be skipped.</>}
              {confirmPlan.plan.invalidCells.length > 0 && <> {confirmPlan.plan.invalidCells.length} cell(s) are not valid numbers and will be flagged, not applied.</>}
            </span>
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            {confirmPlan.plan.newRows.length > 0 && <Badge variant="info">{confirmPlan.plan.newRows.length} new row{confirmPlan.plan.newRows.length === 1 ? '' : 's'}</Badge>}
            {confirmPlan.plan.skippedRows > 0 && <Badge variant="danger">{confirmPlan.plan.skippedRows} unknown code{confirmPlan.plan.skippedRows === 1 ? '' : 's'}</Badge>}
            {confirmPlan.plan.skippedFrozen > 0 && <Badge variant="neutral">{confirmPlan.plan.skippedFrozen} frozen skipped</Badge>}
            {confirmPlan.plan.invalidCells.length > 0 && <Badge variant="danger">{confirmPlan.plan.invalidCells.length} invalid</Badge>}
            {confirmPlan.plan.unmatchedMonthColumns > 0 && <Badge variant="warning">{confirmPlan.plan.unmatchedMonthColumns} month column{confirmPlan.plan.unmatchedMonthColumns === 1 ? '' : 's'} not in this version</Badge>}
          </div>
        </ConfirmDialog>
      )}

      <div className="overflow-x-auto rounded-lg border border-neutral-200">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="overflow-y-auto"
          style={{ height, maxWidth: '100%' }}
        >
          <table className="min-w-full border-collapse text-xs" aria-describedby={report ? `${gridId}-report` : undefined}>
            <thead className="sticky top-0 z-20 bg-neutral-50">
              <tr>
                <th className="sticky left-0 z-30 bg-neutral-50 border-b border-r border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600 min-w-32">
                  {rowHeaderLabel}
                </th>
                {cols.map((c) => (
                  <th
                    key={c.id}
                    className={cn(
                      'border-b border-r border-neutral-200 px-2 py-2 text-right text-[11px] font-semibold min-w-24',
                      highlightColIds?.has(c.id)
                        ? 'bg-primary-50 border-b-2 border-b-primary-400 text-primary-700'
                        : 'text-neutral-600',
                    )}
                  >
                    {c.label}
                  </th>
                ))}
                <th className="border-b border-neutral-200 px-2 py-2 text-right text-[11px] font-semibold text-primary-700 bg-primary-50">
                  {rowTotalLabel}
                </th>
              </tr>
            </thead>
            <tbody>
              {topSpacer > 0 && (
                <tr aria-hidden style={{ height: topSpacer }}>
                  <td colSpan={cols.length + 2} className="p-0 border-0" />
                </tr>
              )}
              {rows.slice(startIdx, endIdx).map((row, i) => {
                const rowIdx = startIdx + i
                return (
                  <tr key={row.id} style={{ height: ROW_HEIGHT }}>
                    <td className="sticky left-0 z-10 bg-white border-b border-r border-neutral-200 px-3 py-1.5 text-left font-medium text-neutral-800">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate">{row.label}</span>
                        {!readOnly && rowActions?.(row)}
                      </div>
                    </td>
                    {cols.map((col, colIdx) => {
                      const key = cellKey(row.id, col.id)
                      const isFrozen = frozen.has(key)
                      const invalidRaw = invalidByKey.get(key)
                      return (
                        <MatrixCell
                          key={col.id}
                          cellKeyAttr={key}
                          registerRef={registerCellRef(key)}
                          value={cells.get(key) ?? 0}
                          hasEntry={cells.has(key)}
                          frozen={isFrozen}
                          readOnly={readOnly}
                          locked={locked}
                          modified={isCellModified(rowIdx, colIdx)}
                          invalidRaw={invalidRaw}
                          selected={isInSelection(rowIdx, colIdx)}
                          formatValue={formatValue}
                          onSelect={(extend) => focusCell(rowIdx, colIdx, extend)}
                          onCommit={(v) => setCellValue(rowIdx, colIdx, v)}
                          onHistoryClick={onCellHistoryClick
                            ? (e) => { e.stopPropagation(); onCellHistoryClick(row.id, col.id, e.currentTarget) }
                            : undefined}
                          onNavigate={(dir) => {
                            const delta: Record<typeof dir, [number, number]> = {
                              up: [-1, 0], down: [1, 0], left: [0, -1], right: [0, 1],
                              tab: [0, 1], enter: [1, 0],
                            }
                            const [dr, dc] = delta[dir]
                            focusCell(rowIdx + dr, colIdx + dc, false, true)
                          }}
                        />
                      )
                    })}
                    <td className="border-b border-neutral-200 px-2 py-1.5 text-right font-mono text-xs bg-primary-50 text-primary-800 font-semibold">
                      {formatValue(rowTotal(rowIdx))}
                    </td>
                  </tr>
                )
              })}
              {bottomSpacer > 0 && (
                <tr aria-hidden style={{ height: bottomSpacer }}>
                  <td colSpan={cols.length + 2} className="p-0 border-0" />
                </tr>
              )}
            </tbody>
            {/* A real <tfoot> (not a plain <tr> in <tbody>) — sticky-to-viewport
                positioning only works reliably on thead/tfoot, not on an
                arbitrary <tr>, so this is what keeps the totals row pinned to
                the bottom of the scroll container without overlapping the
                last data row. */}
            <tfoot className="sticky bottom-0 z-20">
              <tr className="bg-neutral-50">
                <td className="sticky left-0 z-30 bg-neutral-50 border-t border-r border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-600">
                  {colTotalLabel}
                </td>
                {cols.map((_c, colIdx) => (
                  <td key={colIdx} className="border-t border-r border-neutral-200 px-2 py-1.5 text-right font-mono text-xs bg-neutral-100 text-neutral-800 font-semibold">
                    {formatValue(colTotal(colIdx))}
                  </td>
                ))}
                <td className="border-t border-neutral-200 px-2 py-1.5 text-right font-mono text-xs bg-primary-100 text-primary-900 font-bold">
                  {formatValue(grandTotal)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
  )
}

const EMPTY_FROZEN: ReadonlySet<string> = new Set()

// ── Cell ─────────────────────────────────────────────────────────────────

type NavDir = 'up' | 'down' | 'left' | 'right' | 'tab' | 'enter'

function MatrixCell({
  cellKeyAttr, registerRef, value, hasEntry, frozen, readOnly, locked, modified, invalidRaw, selected, formatValue,
  onSelect, onCommit, onNavigate, onHistoryClick,
}: {
  /** `${rowId}::${colId}` — exposed as data-testid for automated/manual verification, not used by the component itself. */
  cellKeyAttr: string
  /** Registers this cell's focusable DOM node so keyboard navigation (Tab/Arrow/Enter) can imperatively move real focus to it — see the focus-sync effect in MatrixGrid. */
  registerRef: (el: HTMLElement | null) => void
  value: number
  hasEntry: boolean
  frozen: boolean
  readOnly: boolean
  /** True while a >100-cell paste confirmation is open (see MatrixGrid's `locked`). Disables the input so it can neither receive nor commit an edit until the confirmation is resolved. */
  locked: boolean
  modified: boolean
  invalidRaw: string | undefined
  selected: boolean
  formatValue: (n: number) => string
  /** Mouse click only — `extend=true` on a shift-click grows the selection range from the existing anchor. Deliberately NOT driven by focus events: a focus event fires on the just-clicked cell before its click event does, so reading shiftKey there would reset the anchor to the new cell before the extend could apply. */
  onSelect: (extend: boolean) => void
  onCommit: (v: number) => void
  onNavigate: (dir: NavDir) => void
  /** See MatrixGridProps.onCellHistoryClick — pre-bound to this cell's row/col by the caller. */
  onHistoryClick?: (e: ReactMouseEvent<HTMLButtonElement>) => void
}) {
  // Sync external value changes (undo/redo/paste/reset) into the local edit
  // buffer. Done as a render-phase state adjustment (React's documented
  // "adjusting state when a prop changes" pattern) rather than a setState-
  // in-effect, which the stricter react-hooks lint rule flags as a
  // cascading-render risk.
  const external = hasEntry ? String(value) : ''
  const [local, setLocal] = useState(external)
  const [syncedExternal, setSyncedExternal] = useState(external)
  if (external !== syncedExternal) {
    setSyncedExternal(external)
    setLocal(external)
  }

  const commit = () => {
    const trimmed = local.trim()
    if (trimmed === '') {
      onCommit(0)
      return
    }
    const n = parseFloat(trimmed.replace(/[,$]/g, ''))
    onCommit(Number.isFinite(n) ? n : 0)
  }

  const nonEditable = readOnly || frozen

  if (nonEditable) {
    return (
      <td
        className={cn(
          'relative border-b border-r border-neutral-200 px-2 py-1.5 text-right font-mono text-xs',
          frozen ? 'bg-neutral-100 text-neutral-500' : 'bg-neutral-50 text-neutral-600',
          selected && 'ring-2 ring-inset ring-primary-400',
        )}
        title={frozen ? 'Frozen — not editable' : undefined}
        tabIndex={frozen ? 0 : undefined}
        data-testid={`matrix-cell-${cellKeyAttr}`}
        data-matrix-input="true"
        ref={registerRef}
        onClick={(e) => onSelect(e.shiftKey)}
        onKeyDown={frozen ? (e) => {
          if (e.key === 'Enter') { e.preventDefault(); onNavigate('enter') }
          else if (e.key === 'Tab') { e.preventDefault(); onNavigate(e.shiftKey ? 'left' : 'tab') }
          else if (e.key === 'ArrowUp') { e.preventDefault(); onNavigate('up') }
          else if (e.key === 'ArrowDown') { e.preventDefault(); onNavigate('down') }
          else if (e.key === 'ArrowLeft') { e.preventDefault(); onNavigate('left') }
          else if (e.key === 'ArrowRight') { e.preventDefault(); onNavigate('right') }
        } : undefined}
      >
        {frozen && <Lock aria-hidden className="absolute top-0.5 left-0.5 h-2.5 w-2.5 text-neutral-400" />}
        {frozen && <span className="sr-only">Frozen — not editable.</span>}
        {hasEntry ? formatValue(value) : '—'}
        {onHistoryClick && (
          <button
            type="button"
            onClick={onHistoryClick}
            aria-label="View change history"
            title="View change history"
            className="absolute bottom-0 right-0 p-0.5 text-neutral-300 hover:text-primary-600"
          >
            <History aria-hidden className="h-2.5 w-2.5" />
          </button>
        )}
      </td>
    )
  }

  return (
    <td
      className={cn(
        'relative border-b border-r border-neutral-200 p-0',
        invalidRaw ? 'bg-danger-50' : modified ? 'bg-amber-50' : 'bg-white',
        selected && 'ring-2 ring-inset ring-primary-400',
      )}
    >
      {modified && !invalidRaw && (
        <span
          aria-hidden
          className="pointer-events-none absolute top-0 right-0 h-0 w-0 border-t-[6px] border-l-[6px] border-l-transparent border-t-amber-500"
        />
      )}
      {invalidRaw && (
        <AlertTriangle aria-hidden className="pointer-events-none absolute top-0.5 right-0.5 h-3 w-3 text-danger-600" />
      )}
      <input
        type="text"
        inputMode="decimal"
        value={local}
        disabled={locked}
        aria-invalid={!!invalidRaw}
        data-testid={`matrix-cell-${cellKeyAttr}`}
        data-matrix-input="true"
        ref={registerRef}
        aria-label={invalidRaw ? `Invalid value pasted: "${invalidRaw}" is not a number` : undefined}
        onClick={(e) => onSelect(e.shiftKey)}
        // Select the whole value on focus so clicking (or Tab/arrow-navigating)
        // into a cell that already holds a number lets you just type to replace
        // it — Excel behaviour. Without this, typing into a populated cell
        // appends to the existing text ("251" + "300" -> "251300"), which makes
        // direct entry feel broken and pushes users toward paste-only editing.
        onFocus={(e) => e.currentTarget.select()}
        onChange={(ev) => setLocal(ev.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commit(); onNavigate('enter') }
          else if (e.key === 'Tab') { e.preventDefault(); commit(); onNavigate(e.shiftKey ? 'left' : 'tab') }
          else if (e.key === 'ArrowUp') { e.preventDefault(); commit(); onNavigate('up') }
          else if (e.key === 'ArrowDown') { e.preventDefault(); commit(); onNavigate('down') }
          else if (e.key === 'ArrowLeft' && (e.currentTarget.selectionStart === 0)) { commit(); onNavigate('left') }
          else if (e.key === 'ArrowRight' && (e.currentTarget.selectionStart === local.length)) { commit(); onNavigate('right') }
        }}
        placeholder="0"
        className="w-full text-right font-mono text-xs px-2 py-1.5 bg-transparent border-0 focus:outline-none focus:ring-1 focus:ring-primary-500 focus:bg-white disabled:cursor-not-allowed disabled:opacity-60"
      />
      {invalidRaw && <span className="sr-only">{`Pasted value "${invalidRaw}" was not a valid number and was not applied.`}</span>}
      {onHistoryClick && (
        <button
          type="button"
          onClick={onHistoryClick}
          aria-label="View change history"
          title="View change history"
          className="absolute bottom-0 right-0 p-0.5 text-neutral-300 hover:text-primary-600"
        >
          <History aria-hidden className="h-2.5 w-2.5" />
        </button>
      )}
    </td>
  )
}

