// Sales Forecast — continuous grid redesign (Task 7). The forecast is now
// a single living table (mrp_demand_series) edited directly, not a stack of
// versioned snapshots: rows = materials, columns = a rolling month range
// (default [current_month-3, current_month+24]) fetched from seriesApi
// (Task 6). Past columns (month < current_month) are read-only — they're
// history, not something a planner edits going forward. The
// [current_month, current_month+18) window is highlighted (not gated) as a
// visual reminder of what "Generate Outlook" would currently freeze.
// Edits autosave (debounced) straight to the series; there is no
// version switcher / New / Save Draft / Confirm button here anymore — see
// GenerateOutlookModal.tsx for the one write action that still produces an
// immutable snapshot (a confirmed ForecastVersion, for MPS to consume).
//
// Reuses ForecastPage.tsx's proven patterns wherever the shape carries over:
// MatrixGrid wiring (frozenKeys/focusRequest/rowActions/resolveMaterial),
// the finished-goods MaterialPicker Add-Product flow, and the
// committed-vs-live CellValueMap split for dirty-diffing (renamed
// `committed` here since it's no longer literally "the server's last
// snapshot" — see flushSave below, which updates it in place after each
// autosave instead of refetching/remounting the grid).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, Check, Loader2, Lock, Sparkles, X } from 'lucide-react'
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { MatrixGrid, type GridRow as MatrixRow, type GridCol as MatrixCol } from '@/components/MatrixGrid'
import { cellKey, parseCellKey } from '@/components/matrixGrid/pasteLogic'
import type { CellValueMap } from '@/components/matrixGrid/history'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { MaterialPicker } from '@/pages/consignment/MaterialPicker'
import { materialsApi, type MaterialOption } from '@/lib/materials'
import { seriesApi, type GridResponse } from './seriesApi'
import { GenerateOutlookModal } from './GenerateOutlookModal'
import { CellHistoryPopover } from './CellHistoryPopover'

const AUTOSAVE_DEBOUNCE_MS = 1200
const OUTLOOK_HORIZON_MONTHS = 18

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
}

/** 'YYYY-MM' for the current month, in the browser's local time — no date lib. */
function currentMonthStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** Add (or subtract, via a negative delta) whole months to a 'YYYY-MM' string. */
function addMonths(month: string, delta: number): string {
  const [y, m] = month.split('-').map(Number)
  const d = new Date(Date.UTC(y, m - 1 + delta, 1))
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}

function buildBaseline(grid: GridResponse | undefined): CellValueMap {
  const map: CellValueMap = new Map()
  if (!grid) return map
  for (const row of grid.rows) {
    for (const month of grid.months) {
      const raw = row.cells[month]
      const n = raw !== undefined ? Number(raw) : 0
      if (n !== 0) map.set(cellKey(row.material_code, month), n)
    }
  }
  return map
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

function SaveIndicator({ state }: { state: SaveState }) {
  if (state === 'saving') {
    return <span className="flex items-center gap-1 text-xs text-neutral-500"><Loader2 className="h-3 w-3 animate-spin" /> Saving…</span>
  }
  if (state === 'saved') {
    return <span className="flex items-center gap-1 text-xs text-success-700"><Check className="h-3 w-3" /> Saved</span>
  }
  if (state === 'error') {
    return <span className="flex items-center gap-1 text-xs text-danger-600"><AlertCircle className="h-3 w-3" /> Save failed — edit again to retry</span>
  }
  return null
}

export default function SalesForecastPage() {
  const toasts = useToasts()
  const permsQuery = usePermissions()
  const canWriteForecast = !!(permsQuery.data?.permissions['mrp.demand.write'] || permsQuery.data?.permissions['data_maintenance'])

  const currentMonth = useMemo(() => currentMonthStr(), [])
  const rangeFrom = useMemo(() => addMonths(currentMonth, -3), [currentMonth])
  const rangeTo = useMemo(() => addMonths(currentMonth, 24), [currentMonth])
  const windowEndExclusive = useMemo(() => addMonths(currentMonth, OUTLOOK_HORIZON_MONTHS), [currentMonth])

  // committed = last known persisted state (server load, then kept in sync
  // by flushSave after each successful autosave — see below). liveCells =
  // what's actually on screen right now. Diffing the two is how dirty cells
  // are found, same split ForecastPage used for its baseline/liveCells, but
  // updated in place here instead of via a full grid refetch: refetching
  // would bump gridKey and remount MatrixGrid (see its own header comment —
  // "remount with a different key to load a different dataset"), which
  // would wipe the undo stack and drop focus mid-type on every autosave.
  const [committed, setCommitted] = useState<CellValueMap>(new Map())
  const [liveCells, setLiveCells] = useState<CellValueMap>(new Map())
  const [syncedBaseline, setSyncedBaseline] = useState<CellValueMap>(new Map())
  const [addedRows, setAddedRows] = useState<Map<string, MaterialOption>>(new Map())
  // Codes added this session whose placeholder (or a real edit) has already
  // been autosaved — once persisted, "Add Product" no longer offers the
  // quick-remove X for them (undoing that would need a real delete, not
  // implemented here; see handleRemoveAddedRow).
  const [persistedAddedCodes, setPersistedAddedCodes] = useState<Set<string>>(new Set())
  const [syncedGridKeyForAddedRows, setSyncedGridKeyForAddedRows] = useState<string | null>(null)
  const [focusRequest, setFocusRequest] = useState<{ rowId: string; colId?: string } | null>(null)
  const [clearRowId, setClearRowId] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [outlookOpen, setOutlookOpen] = useState(false)
  const [outlookBusy, setOutlookBusy] = useState(false)
  const [outlookError, setOutlookError] = useState<string | null>(null)
  const [historyPopover, setHistoryPopover] = useState<{
    materialCode: string
    materialLabel: string
    month: string
    rect: { top: number; left: number; bottom: number; right: number }
  } | null>(null)

  // Full materials master (finished goods), fetched once — backs both the
  // row-creating full-table paste (resolveMaterial) and the past-column
  // frozenKeys cross-product below, so a brand new row a planner pastes in
  // still has its past months locked even though it wasn't part of the
  // server's grid at load time. Only needed once editing is possible.
  const materialsAllQuery = useQuery({
    queryKey: ['forecast-materials-all'],
    queryFn: () => materialsApi.listAll(),
    enabled: canWriteForecast,
    staleTime: 5 * 60 * 1000,
  })
  const materialsByCode = useMemo(() => {
    const map = new Map<string, MaterialOption>()
    for (const m of materialsAllQuery.data ?? []) map.set(m.code, m)
    return map
  }, [materialsAllQuery.data])
  const resolveMaterial = useCallback((code: string) => {
    const m = materialsByCode.get(code)
    return m ? { id: m.code, label: m.name ? `${m.code} — ${m.name}` : m.code } : undefined
  }, [materialsByCode])

  const gridQuery = useQuery({
    queryKey: ['sales-forecast-grid', rangeFrom, rangeTo],
    queryFn: () => seriesApi.getGrid(rangeFrom, rangeTo),
  })

  const baseline = useMemo(() => buildBaseline(gridQuery.data), [gridQuery.data])

  // Remounts MatrixGrid exactly once, when the initial fetch lands (see
  // gridQuery.dataUpdatedAt below) — never again afterward, since nothing
  // on this page invalidates/refetches the grid query post-load (autosave
  // updates `committed` directly instead; see flushSave).
  const gridKey = `${rangeFrom}::${rangeTo}::${gridQuery.dataUpdatedAt}`

  if (baseline !== syncedBaseline) {
    setSyncedBaseline(baseline)
    setCommitted(baseline)
    setLiveCells(baseline)
  }

  if (gridKey !== syncedGridKeyForAddedRows) {
    setSyncedGridKeyForAddedRows(gridKey)
    if (addedRows.size > 0) setAddedRows(new Map())
    if (persistedAddedCodes.size > 0) setPersistedAddedCodes(new Set())
    if (focusRequest) setFocusRequest(null)
  }

  const dirtyCells = useMemo(() => {
    const out: { material_code: string; month: string; qty: number }[] = []
    const keys = new Set<string>([...committed.keys(), ...liveCells.keys()])
    for (const key of keys) {
      const b = committed.get(key) ?? 0
      const c = liveCells.get(key) ?? 0
      if (b !== c) {
        const { rowId, colId } = parseCellKey(key)
        out.push({ material_code: rowId, month: colId, qty: c })
      }
    }
    return out
  }, [committed, liveCells])

  const matrixRows: MatrixRow[] = useMemo(() => {
    const serverRows = gridQuery.data?.rows ?? []
    const serverCodes = new Set(serverRows.map((r) => r.material_code))
    const extraRows = [...addedRows.values()].filter((m) => !serverCodes.has(m.code))
    return [
      ...serverRows.map((r) => ({ id: r.material_code, label: r.name ?? r.material_code })),
      ...extraRows.map((m) => ({ id: m.code, label: m.name ? `${m.code} — ${m.name}` : m.code })),
    ]
  }, [gridQuery.data, addedRows])
  const matrixCols: MatrixCol[] = useMemo(
    () => (gridQuery.data?.months ?? []).map((m) => ({ id: m, label: m })),
    [gridQuery.data],
  )

  // Past-column read-only enforcement — every material x every month before
  // currentMonth. Built from the union of visible rows AND the full
  // materials master (when loaded) rather than matrixRows alone, so a row a
  // full-table paste creates mid-session still has its past cells frozen —
  // planFullTablePaste checks frozenKeys by (code, month) regardless of
  // whether the row existed in `rows` when frozenKeys was built.
  const pastMonths = useMemo(
    () => (gridQuery.data?.months ?? []).filter((m) => m < currentMonth),
    [gridQuery.data, currentMonth],
  )
  const frozenKeys = useMemo(() => {
    const s = new Set<string>()
    if (pastMonths.length === 0) return s
    const codes = new Set<string>()
    for (const r of matrixRows) codes.add(r.id)
    for (const code of materialsByCode.keys()) codes.add(code)
    for (const code of codes) for (const m of pastMonths) s.add(cellKey(code, m))
    return s
  }, [pastMonths, matrixRows, materialsByCode])

  const highlightColIds = useMemo(() => {
    const s = new Set<string>()
    for (const c of matrixCols) if (c.id >= currentMonth && c.id < windowEndExclusive) s.add(c.id)
    return s
  }, [matrixCols, currentMonth, windowEndExclusive])

  // ── Autosave ──────────────────────────────────────────────────────────
  // flushRef always points at a closure from the most recent render (synced
  // by the effect right below) so the debounce timer — which lives outside
  // React's render cycle — never calls a stale version holding last
  // render's dirtyCells/addedRows. Assigning the ref itself happens in an
  // effect, not directly during render (the repo's stricter react-hooks
  // lint rule disallows a bare ref mutation in the render body); the effect
  // has no dependency array so it re-syncs after every render, same as the
  // canonical "useEventCallback" pattern.
  const flushRef = useRef<() => void>(() => {})
  const saveTimer = useRef<number | null>(null)
  const savedResetTimer = useRef<number | null>(null)

  const doFlush = async () => {
    const months = gridQuery.data?.months ?? []
    if (months.length === 0) return
    // A row added via "Add Product" that the planner hasn't typed anything
    // into yet has no dirty cell, so on its own it would never appear in
    // the autosave payload and the row would vanish if the grid ever did
    // reload — send one explicit qty=0 placeholder (first non-past month)
    // per such row so it survives as a real, all-zero series row. Same
    // reasoning ForecastPage's handleSaveDraft used for Save Draft.
    const anchorMonth = months.find((m) => m >= currentMonth) ?? months[months.length - 1]
    const dirtyRowCodes = new Set(dirtyCells.map((c) => c.material_code))
    const placeholders = [...addedRows.keys()]
      .filter((code) => !dirtyRowCodes.has(code) && !persistedAddedCodes.has(code))
      .map((code) => ({ material_code: code, month: anchorMonth, qty: 0 }))
    const cellsToSave = [...dirtyCells, ...placeholders]
    if (cellsToSave.length === 0) return

    setSaveState('saving')
    try {
      const res = await seriesApi.upsertCells(cellsToSave)
      setCommitted((prev) => {
        const next = new Map(prev)
        for (const c of cellsToSave) {
          const key = cellKey(c.material_code, c.month)
          if (c.qty === 0) next.delete(key)
          else next.set(key, c.qty)
        }
        return next
      })
      if (placeholders.length > 0) {
        setPersistedAddedCodes((prev) => {
          const next = new Set(prev)
          for (const p of placeholders) next.add(p.material_code)
          return next
        })
      }
      setSaveState('saved')
      toasts.success(`${res.upserted} cell${res.upserted === 1 ? '' : 's'} saved.`)
      if (savedResetTimer.current) window.clearTimeout(savedResetTimer.current)
      savedResetTimer.current = window.setTimeout(() => setSaveState('idle'), 3000)
    } catch (err) {
      setSaveState('error')
      toasts.error(errMsg(err, 'Autosave failed — please retry.'))
    }
  }

  useEffect(() => {
    flushRef.current = doFlush
  })

  const scheduleSave = useCallback(() => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => { void flushRef.current() }, AUTOSAVE_DEBOUNCE_MS)
  }, [])

  useEffect(() => () => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    if (savedResetTimer.current) window.clearTimeout(savedResetTimer.current)
  }, [])

  // Never lose an edit silently to a closed tab — warn while an autosave is
  // still pending or in flight.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirtyCells.length > 0 || saveState === 'saving') {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirtyCells.length, saveState])

  const handleGridChange = useCallback((next: CellValueMap) => {
    setLiveCells(next)
    scheduleSave()
  }, [scheduleSave])

  /** "Add Product" — appends a row immediately (no server round trip) and
   *  focuses its first editable cell; the row itself is persisted by the
   *  next autosave (see flushRef's placeholder logic above). */
  function handleAddProduct(m: MaterialOption) {
    const alreadyServerRow = (gridQuery.data?.rows ?? []).some((r) => r.material_code === m.code)
    if (!alreadyServerRow && !addedRows.has(m.code)) {
      setAddedRows((prev) => {
        const next = new Map(prev)
        next.set(m.code, m)
        return next
      })
    }
    setFocusRequest({ rowId: m.code, colId: currentMonth })
    scheduleSave()
  }

  /** Removes a row that only ever existed locally and was never autosaved —
   *  a mis-pick fix. Not offered once persisted (see rowActions below) since
   *  undoing that would need a real delete, which this page doesn't have. */
  function handleRemoveAddedRow(code: string) {
    setAddedRows((prev) => {
      if (!prev.has(code)) return prev
      const next = new Map(prev)
      next.delete(code)
      return next
    })
    setClearRowId(code)
    setFocusRequest((f) => (f?.rowId === code ? null : f))
  }

  async function handleGenerateOutlook(anchorMonth: string, horizonMonths: number) {
    setOutlookBusy(true)
    setOutlookError(null)
    try {
      const created = await seriesApi.generateOutlook(anchorMonth, horizonMonths)
      setOutlookOpen(false)
      toasts.success(`Outlook ${created.version_no} generated — run it from Production Plan when ready.`)
    } catch (err) {
      setOutlookError(errMsg(err, 'Could not generate the outlook.'))
    } finally {
      setOutlookBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-neutral-900">Sales Forecast</h1>
          <p className="text-xs text-neutral-500">
            A continuous, always-current forecast — edits save automatically. Months before {currentMonth} are locked.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <SaveIndicator state={saveState} />
          {canWriteForecast && (
            <Button type="button" size="sm" onClick={() => { setOutlookError(null); setOutlookOpen(true) }}>
              <Sparkles className="h-3.5 w-3.5" /> Generate Outlook
            </Button>
          )}
        </div>
      </div>

      {gridQuery.isError && (
        <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {errMsg(gridQuery.error, 'Could not load the forecast grid.')}
        </p>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {canWriteForecast && (
            <div className="w-56">
              <MaterialPicker
                value=""
                onSelect={handleAddProduct}
                onClear={() => { /* trigger never shows a value — nothing to clear */ }}
                placeholder="Add Product…"
              />
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-4 text-[11px] text-neutral-500">
          <span className="flex items-center gap-1"><Lock className="h-3 w-3 text-neutral-400" /> Past months are locked</span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm border border-primary-300 bg-primary-100" />
            Outlook window ({currentMonth} – {addMonths(currentMonth, OUTLOOK_HORIZON_MONTHS - 1)})
          </span>
        </div>
      </div>

      {gridQuery.isLoading ? (
        <p role="status" className="py-12 text-center text-sm text-neutral-400">Loading grid…</p>
      ) : gridQuery.data && (
        <>
          {matrixRows.length === 0 && (
            <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              No products yet. Add one above, or paste a full forecast table copied from Excel (a product-code column
              creates the rows for you — no need to add products by hand first).
            </p>
          )}
          <MatrixGrid
            key={gridKey}
            rows={matrixRows}
            cols={matrixCols}
            value={liveCells}
            onChange={handleGridChange}
            frozenKeys={frozenKeys}
            readOnly={!canWriteForecast}
            height={560}
            rowHeaderLabel="Product"
            rowTotalLabel="Total"
            colTotalLabel="Monthly Sum"
            formatValue={formatQty}
            focusRequest={focusRequest}
            onFocusRequestHandled={() => setFocusRequest(null)}
            clearRowId={clearRowId}
            onRowCleared={() => setClearRowId(null)}
            resolveMaterial={canWriteForecast ? resolveMaterial : undefined}
            highlightColIds={highlightColIds}
            onCellHistoryClick={(rowId, colId, anchorEl) => {
              const rect = anchorEl.getBoundingClientRect()
              const row = matrixRows.find((r) => r.id === rowId)
              setHistoryPopover({
                materialCode: rowId,
                materialLabel: row?.label ?? rowId,
                month: colId,
                rect: { top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right },
              })
            }}
            rowActions={(row) => (
              addedRows.has(row.id) && !persistedAddedCodes.has(row.id) ? (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); handleRemoveAddedRow(row.id) }}
                  aria-label={`Remove ${row.label} — not yet saved`}
                  title="Remove — not yet saved"
                  className="shrink-0 text-neutral-300 hover:text-danger-500"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null
            )}
          />
        </>
      )}

      {outlookOpen && (
        <GenerateOutlookModal
          busy={outlookBusy}
          error={outlookError}
          onClose={() => setOutlookOpen(false)}
          onGenerate={handleGenerateOutlook}
        />
      )}

      {historyPopover && (
        <CellHistoryPopover
          materialCode={historyPopover.materialCode}
          materialLabel={historyPopover.materialLabel}
          month={historyPopover.month}
          anchorRect={historyPopover.rect}
          onClose={() => setHistoryPopover(null)}
        />
      )}

      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  )
}
