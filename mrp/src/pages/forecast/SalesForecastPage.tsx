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
import { Button, Badge } from '@uniops/shell'
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
import { bomStatusApi } from './bomStatusApi'

const AUTOSAVE_DEBOUNCE_MS = 1200
const OUTLOOK_HORIZON_MONTHS = 18
const EMPTY_STRING_SET: ReadonlySet<string> = new Set()

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
  // Codes added this session that are known to be durably persisted —
  // "Add Product" stops offering the quick-remove X for them (undoing that
  // would need a real delete, not implemented here; see
  // handleRemoveAddedRow). An added row with no value typed in yet is NOT
  // persisted (mrp_demand_series is sparse — see doFlush's comment) and
  // must never land in this set purely for being added; today nothing adds
  // a code here at all (a genuinely-saved added row still keeps its X too —
  // a separate, minor gap, not the vanishing-row bug this set exists to
  // avoid).
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

  // This page treats its very first successful fetch as the one-and-only
  // load: after that, autosave (flushSave/doFlush below) keeps `committed`
  // in sync with the server directly, so nothing here ever calls
  // invalidateQueries/refetch. A *background* refetch is a different risk
  // though — TanStack Query's defaults (App.tsx only turns off
  // refetchOnWindowFocus) still include refetchOnReconnect: true, so a
  // network blip mid-edit would otherwise silently refetch this query. Any
  // resulting new `gridQuery.data` reference would (a) re-run the
  // baseline-sync block below, overwriting the in-progress `liveCells`
  // state with the server's snapshot — silently discarding not-yet-flushed
  // edits from the dirty-diff *without even changing what MatrixGrid is
  // still visibly showing*, which is worse than a remount (an invisible
  // trust gap, not just a visible reset) — and (b) previously changed
  // `gridKey` (see below), remounting MatrixGrid and wiping its undo stack.
  // Disabling both background-refetch triggers is the actual fix; the
  // range-only `gridKey` (not tied to `dataUpdatedAt`) is a second,
  // independent guard so a remount can't happen even if some future change
  // to this file ever does call refetch.
  const gridQuery = useQuery({
    queryKey: ['sales-forecast-grid', rangeFrom, rangeTo],
    queryFn: () => seriesApi.getGrid(rangeFrom, rangeTo),
    staleTime: Infinity,
    refetchOnReconnect: false,
  })

  const baseline = useMemo(() => buildBaseline(gridQuery.data), [gridQuery.data])

  // Deliberately just the requested range, not gridQuery.dataUpdatedAt —
  // see the comment above. MatrixGrid mounts once, when `gridQuery.data`
  // first becomes truthy, and never again for the life of this page.
  const gridKey = `${rangeFrom}::${rangeTo}`

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

  // No-BOM flagging (spec §8b): a finished good can be forecast before its
  // BOM exists in mdm-api — allowed, but flagged. Sorted so the query key is
  // stable across renders regardless of row insertion order (react-query
  // hashes the key structurally, but a stable order also keeps this cheap to
  // eyeball in devtools). "Has a BOM" is a single source (mdm-api's
  // /boms/exist) shared with MPS — see bomStatusApi.ts.
  const gridProductCodes = useMemo(
    () => [...new Set(matrixRows.map((r) => r.id))].sort(),
    [matrixRows],
  )
  const bomStatusQuery = useQuery({
    queryKey: ['forecast-bom-status', gridProductCodes],
    queryFn: () => bomStatusApi.withBom(gridProductCodes),
    enabled: gridProductCodes.length > 0,
    staleTime: 5 * 60 * 1000,
  })
  const noBomRowIds = useMemo(() => {
    const withBom = bomStatusQuery.data
    if (!withBom) return EMPTY_STRING_SET
    const s = new Set<string>()
    for (const code of gridProductCodes) if (!withBom.has(code)) s.add(code)
    return s
  }, [gridProductCodes, bomStatusQuery.data])

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
    // into yet has no dirty cell — and deliberately sends NOTHING for it.
    // An earlier version of this function sent an explicit qty=0
    // "placeholder" cell so the row would "survive as a real, all-zero
    // series row," but mrp_demand_series is SPARSE: upsert_cells treats
    // qty==0 as a delete/no-op (see app/services/demand_series.py), so that
    // placeholder was never actually persisted — it silently vanished on
    // the next page load while this page had already moved the row into
    // persistedAddedCodes, dropping its quick-remove X in the meantime. An
    // untouched added row simply stays local-only (keeps the X below) until
    // a real edit produces a dirty cell for its code.
    const cellsToSave = dirtyCells
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
   *  focuses its first editable cell. The row is NOT persisted on its own;
   *  it only becomes durable once the planner types a value into one of its
   *  cells, which then flows into the next autosave like any other edit
   *  (see doFlush). */
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
            tintRowIds={noBomRowIds}
            rowBadge={(row) => (
              noBomRowIds.has(row.id) ? (
                // Badge (packages/shell) doesn't spread rest props onto its
                // <span> — a `title` passed directly to it is silently
                // dropped. Wrap it in a plain span carrying the tooltip.
                <span title="No approved BOM found yet for this product — forecast entry still works.">
                  <Badge variant="warning" className="shrink-0">
                    No BOM
                  </Badge>
                </span>
              ) : null
            )}
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
