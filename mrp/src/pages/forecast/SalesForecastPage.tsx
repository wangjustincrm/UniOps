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
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Check, Eye, Lightbulb, Link2, Loader2, Lock, Sparkles, X } from 'lucide-react'
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
import { forecastApi, type ForecastVersion } from './forecastApi'
import { intentApi, isIntentCode, type IntentProduct } from './intentApi'
import { GenerateOutlookModal } from './GenerateOutlookModal'
import { OutlookViewerModal } from './OutlookViewerModal'
import { CellHistoryPopover } from './CellHistoryPopover'
import { AddIntentProductModal } from './AddIntentProductModal'
import { BindIntentModal } from './BindIntentModal'
import { bomStatusApi } from './bomStatusApi'

const AUTOSAVE_DEBOUNCE_MS = 1200
const OUTLOOK_HORIZON_MONTHS = 18
const EMPTY_STRING_SET: ReadonlySet<string> = new Set()
// Responsive grid height floor/margin — see the gridHeight effect below.
const GRID_MIN_HEIGHT = 400
const GRID_BOTTOM_MARGIN = 16

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
}

/** Display-only unit toggle for the grid — the stored/planning unit is
 *  always kg (net-requirement and MPS capacity depend on it); this only
 *  scales what's shown/typed. See DISPLAY_UNIT_STORAGE_KEY below and the
 *  `unitScale`/`formatValue` wiring on <MatrixGrid> further down. */
type DisplayUnit = 'kg' | 't'
const DISPLAY_UNIT_STORAGE_KEY = 'mrp.forecast.displayUnit'

function loadDisplayUnit(): DisplayUnit {
  try {
    const v = window.localStorage.getItem(DISPLAY_UNIT_STORAGE_KEY)
    return v === 'kg' || v === 't' ? v : 't'
  } catch {
    return 't' // localStorage unavailable (e.g. privacy mode) — fall back to the default
  }
}

function formatTonnes(kg: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(kg / 1000)
}

/** Outlooks panel's "Created" column — a full local date+time, since two
 *  outlooks generated the same day (e.g. a redo) are otherwise indistinguishable. */
function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
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
  const queryClient = useQueryClient()
  const permsQuery = usePermissions()
  const canWriteForecast = !!(permsQuery.data?.permissions['mrp.demand.write'] || permsQuery.data?.permissions['data_maintenance'])
  const canViewOutlooks = !!permsQuery.data?.permissions['mrp.report.view']

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
  // Row a SERVER-loaded product only stays visible while it still has at
  // least one non-zero cell in the current edited state (liveCells) — a
  // product cleared to fully-empty disappears from the grid (session-added
  // rows are exempt, see matrixRows below). `initialized` gates this: it
  // flips true the first time the baseline-apply block below actually runs
  // (i.e. once gridQuery.data has loaded and liveCells holds the real
  // server data, not the empty initial Map). Before that, every server row
  // is shown unfiltered — otherwise the empty initial liveCells would make
  // every row look "cleared" for one render and flash-hide the whole grid
  // on first load. Deliberately NOT `liveCells.size > 0` as the guard: that
  // would make cleared rows reappear (falling back to something non-empty)
  // the moment a planner legitimately clears the entire table down to zero.
  const [initialized, setInitialized] = useState(false)
  // Display-only unit for the grid — kg is the canonical/stored unit
  // everywhere else in this file (committed/liveCells/dirtyCells/autosave
  // payload); this only scales what MatrixGrid shows and parses on input,
  // via its `unitScale`/`formatValue` props. See DISPLAY_UNIT_STORAGE_KEY.
  const [displayUnit, setDisplayUnit] = useState<DisplayUnit>(() => loadDisplayUnit())
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
  // Intent products (planned SKUs with no ERP material code yet) — see
  // intentApi.ts's header. addIntentOpen drives AddIntentProductModal;
  // bindTarget (non-null while BindIntentModal is open) is the active
  // intent product's own row, not a session-added-row concept, since a
  // planner can also bind a code that already existed before this visit.
  const [addIntentOpen, setAddIntentOpen] = useState(false)
  const [bindTarget, setBindTarget] = useState<IntentProduct | null>(null)
  // Bumped after every successful bind — folded into `gridKey` below so a
  // bind forces a fresh <MatrixGrid> mount. MatrixGrid is mount-once (see
  // its own header comment and gridKey's comment below): its internal undo
  // history is seeded from the `value` prop exactly once, in a `useState`
  // initializer, and NEVER re-synced from a later prop change. A bind moves
  // real data server-side from the intent code onto a real material code —
  // there is no way to reflect that inside an already-mounted MatrixGrid
  // instance short of remounting it. See fix-round-1 in task-5-report.md
  // (Critical 1/2) for the full incident this replaced.
  const [bindGeneration, setBindGeneration] = useState(0)
  // Outlooks panel (follow-up #2) — the version being viewed read-only in
  // OutlookViewerModal, or null when the panel/modal is closed.
  const [viewingVersion, setViewingVersion] = useState<ForecastVersion | null>(null)
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

  // Confirmed forecast versions ("outlooks") — deliberately the SAME query
  // key ProductionPlanPage.tsx uses for its forecast-version picker
  // (['forecast-versions']) rather than a page-local key, so the one
  // invalidateQueries call in handleGenerateOutlook below refreshes both
  // this panel and Production Plan's picker together (they're two views of
  // the same underlying list). ProductionPlanPage lives in a keep-alive
  // multi-tab shell (react-router v7 keep-alive — every visited tab's
  // component stays mounted, see reference_react_router_v7_keepalive), so
  // without a shared, invalidated key its query would otherwise never
  // refetch on its own after this page generates a new outlook.
  const outlooksQuery = useQuery({
    queryKey: ['forecast-versions'],
    queryFn: () => forecastApi.listVersions(1, 100),
    enabled: canViewOutlooks,
  })
  const confirmedOutlooks = useMemo(() => {
    const items = (outlooksQuery.data?.items ?? []).filter((v) => v.status === 'confirmed')
    // Defensive newest-first sort — the endpoint already returns created_at
    // desc, but this panel is the one place that ordering would be visibly
    // wrong if that ever changed (same defensiveness as
    // CellHistoryPopover's changed_at sort).
    return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
  }, [outlooksQuery.data])

  // Active intent products — keyed by their placeholder INTENT-xxxxxxxx
  // code for O(1) per-row lookup (name override, badge, Bind action). No
  // permission gate: this is display metadata every grid viewer needs
  // (isIntentCode() alone, used for tint/BOM-exclusion, doesn't depend on
  // it — this map only adds the human name + the ability to open Bind).
  const intentQuery = useQuery({
    queryKey: ['intent-products'],
    queryFn: () => intentApi.list(),
  })
  const intentByCode = useMemo(() => {
    const map = new Map<string, IntentProduct>()
    for (const ip of intentQuery.data ?? []) map.set(ip.code, ip)
    return map
  }, [intentQuery.data])

  const baseline = useMemo(() => buildBaseline(gridQuery.data), [gridQuery.data])

  // Deliberately just the requested range plus `bindGeneration` — not
  // gridQuery.dataUpdatedAt (see the comment above: a background refetch
  // must NOT remount this grid, or it would wipe an in-progress edit's undo
  // stack). `bindGeneration` is the one deliberate, user-initiated exception:
  // a successful bind (handleIntentBound below) bumps it on purpose, because
  // that's the only way an already-mounted MatrixGrid ever learns about data
  // that moved server-side. Every other write path on this page (autosave,
  // Add Product/Add intent product, paste) keeps editing the SAME mount.
  const gridKey = `${rangeFrom}::${rangeTo}::${bindGeneration}`

  if (baseline !== syncedBaseline) {
    setSyncedBaseline(baseline)
    setCommitted(baseline)
    setLiveCells(baseline)
    if (!initialized) setInitialized(true)
  }

  // This block also does double duty as the fix for a bind's stale
  // focus-anchor risk: gridKey changes on every bindGeneration bump, so a
  // focusRequest still pointing at the just-bound (now gone) intent code
  // gets cleared here before the remounted MatrixGrid ever sees it —
  // without this, MatrixGrid.tsx's focusRequest effect finds no matching
  // row in the new mount and returns early without ever calling
  // onFocusRequestHandled, leaving the request stuck forever.
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

  const matrixCols: MatrixCol[] = useMemo(
    () => (gridQuery.data?.months ?? []).map((m) => ({ id: m, label: m })),
    [gridQuery.data],
  )

  const matrixRows: MatrixRow[] = useMemo(() => {
    const serverRows = gridQuery.data?.rows ?? []
    const serverCodes = new Set(serverRows.map((r) => r.material_code))
    const extraRows = [...addedRows.values()].filter((m) => !serverCodes.has(m.code))
    // A server-loaded row disappears once it has no non-zero cell left in
    // the CURRENTLY EDITED state (liveCells) — not the server's baseline —
    // so clearing a product's whole row drops it live, without a save round
    // trip. Session-added rows (extraRows, from "Add Product") are always
    // kept: they're the planner's working rows, even before they hold a
    // value. Gated by `initialized` (see its comment above) so the very
    // first render — before liveCells has been seeded from the server at
    // all — shows every server row instead of filtering against an empty map.
    const visibleServerRows = !initialized
      ? serverRows
      : serverRows.filter((r) => matrixCols.some((c) => (liveCells.get(cellKey(r.material_code, c.id)) ?? 0) !== 0))
    // Intent rows: gridQuery.data's `name` is always null for an
    // INTENT-xxxxxxxx code (it comes from resolve_material_names against
    // mdm-api, and an intent product isn't in the materials master by
    // definition) — without this override every intent row would display
    // its unreadable placeholder code instead of the human name the
    // planner gave it. Deliberately just the name (no "CODE — Name"
    // prefix real-material rows get below): the placeholder code is
    // meaningless to a planner, unlike a real ERP code.
    return [
      ...visibleServerRows.map((r) => ({
        id: r.material_code,
        label: intentByCode.get(r.material_code)?.name ?? r.name ?? r.material_code,
      })),
      ...extraRows.map((m) => ({
        id: m.code,
        label: intentByCode.has(m.code) ? (intentByCode.get(m.code)?.name ?? m.code) : (m.name ? `${m.code} — ${m.name}` : m.code),
      })),
    ]
  }, [gridQuery.data, addedRows, initialized, matrixCols, liveCells, intentByCode])

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
  // Intent codes are excluded from this query entirely (not just from the
  // resulting badge) — a planned SKU with no ERP material code yet can
  // never have a BOM, by definition, so "No BOM" would be actively
  // misleading (it means a REAL product whose BOM hasn't been set up yet;
  // see rowBadge below, which renders "Intent" instead for these rows).
  const gridProductCodes = useMemo(
    () => [...new Set(matrixRows.map((r) => r.id).filter((id) => !isIntentCode(id)))].sort(),
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

  const intentRowIds = useMemo(() => {
    const s = new Set<string>()
    for (const r of matrixRows) if (isIntentCode(r.id)) s.add(r.id)
    return s
  }, [matrixRows])

  // Same amber tint mechanism No-BOM rows already use ("not schedulable
  // yet") — an intent row reads the same way for a different reason (no
  // real material code yet, vs. no BOM yet), see rowBadge for how the two
  // are told apart.
  const tintRowIds = useMemo(() => {
    if (intentRowIds.size === 0) return noBomRowIds
    const s = new Set(noBomRowIds)
    for (const id of intentRowIds) s.add(id)
    return s
  }, [noBomRowIds, intentRowIds])

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

  // Persist the display-unit choice across visits. Purely a display/entry
  // preference — never touches committed/liveCells (kg) or the autosave
  // payload, and changing it does not remount <MatrixGrid> (displayUnit is
  // not part of gridKey), so in-progress edits and undo history survive a
  // toggle.
  useEffect(() => {
    try {
      window.localStorage.setItem(DISPLAY_UNIT_STORAGE_KEY, displayUnit)
    } catch {
      // localStorage unavailable — the toggle still works for this session
    }
  }, [displayUnit])

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

  // Responsive grid height (follow-up #3) — replaces the old fixed
  // `height={560}` with "fill from the grid's own top down to near the
  // bottom of the viewport." Measures gridSectionRef's top (its position is
  // stable once the header/toolbar above it have laid out) rather than
  // hand-computing header heights, so this survives any future toolbar
  // change without needing to be touched. GRID_MIN_HEIGHT is the floor for
  // short viewports (matches the follow-up brief's "keep a sensible min
  // ~400px"). Recomputed on window resize and once gridQuery.data first
  // arrives (before that, gridSectionRef isn't mounted yet — see its render
  // site below, gated on `gridQuery.data &&`).
  const gridSectionRef = useRef<HTMLDivElement>(null)
  const [gridHeight, setGridHeight] = useState(GRID_MIN_HEIGHT)
  useEffect(() => {
    function recompute() {
      const el = gridSectionRef.current
      if (!el) return
      const top = el.getBoundingClientRect().top
      const available = window.innerHeight - top - GRID_BOTTOM_MARGIN
      // Fit to content, capped at the viewport — NOT "always fill the
      // viewport". With few products the old fill-everything height left a big
      // empty scroll area AND pushed the Outlooks panel below the fold; with
      // many products the content exceeds `available` so it fills the viewport
      // and scrolls internally (header + Total row stay sticky). 32 = MatrixGrid
      // ROW_HEIGHT; +90 ≈ sticky header + totals row + borders.
      const contentHeight = matrixRows.length * 32 + 90
      setGridHeight(Math.max(160, Math.min(Math.floor(available), contentHeight)))
    }
    recompute()
    window.addEventListener('resize', recompute)
    return () => window.removeEventListener('resize', recompute)
    // gridQuery.data: re-measure once the grid actually mounts (it's absent
    // — null getBoundingClientRect — while the "Loading grid…" text is
    // showing instead). This page never gets a second `data` reference for
    // the life of the mount (see gridQuery's own header comment above), so
    // this isn't a recurring re-measure trigger, just the one that matters.
    // matrixRows.length is included so adding/removing (or clearing) a product
    // re-fits the content-aware height.
  }, [gridQuery.data, matrixRows.length])

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

  /** After a successful "Add intent product" create — seeds the new
   *  placeholder code onto the grid via the exact same session-row
   *  mechanic "Add Product" uses (a MaterialOption shim, since intent
   *  products aren't in the materials master), so autosave/undo/paste all
   *  keep working unmodified, and warms the intent-products cache so the
   *  new row's name/badge/Bind action are correct on the very first
   *  render (no round trip through intentQuery needed). */
  function handleIntentCreated(created: IntentProduct) {
    queryClient.setQueryData<IntentProduct[]>(['intent-products'], (prev) => [created, ...(prev ?? [])])
    handleAddProduct({
      id: created.id, code: created.code, name: created.name,
      spec: null, item_type: null, erp_item_type: null, base_uom: null, is_active: true,
    })
  }

  /** After a successful Bind. mrp-api's bind endpoint already moved every
   *  series cell + change-log row from the intent's placeholder code onto
   *  the real material code, server-side, in one transaction — the only
   *  question here is how the CLIENT learns about it.
   *
   *  An earlier version of this function tried to re-key `committed`/
   *  `liveCells` in place (move the same cell values from the intent code's
   *  keys onto the real code's keys, client-side) to avoid a refetch. That
   *  was wrong on two counts, both filed as Critical findings in fix round
   *  1 (see task-5-report.md): (1) MatrixGrid is mount-once — it seeds its
   *  own internal undo history from the `value` prop exactly once, in a
   *  `useState` initializer (see MatrixGrid.tsx's own header comment), and
   *  never re-syncs from a later prop change, so the re-keyed maps changed
   *  the PARENT's state and nothing the grid actually displayed; the row
   *  looked like it vanished until a hard reload. (2) MatrixGrid hands its
   *  ENTIRE internal map back through `onChange` on every commit — so the
   *  very next edit anywhere on the grid would overwrite `liveCells`
   *  wholesale with the stale (pre-bind) map still keyed to the intent
   *  code, making `dirtyCells` emit qty=0 for the real code (upsert_cells
   *  treats 0 as delete) and the original values back under the intent
   *  code — undoing the migration via ordinary autosave, with no UI path
   *  back since the intent product is now 'bound' and 404/409s out of
   *  `POST .../bind`.
   *
   *  The only correct fix is to remount: refetch the grid so server truth
   *  is in the cache, THEN bump `bindGeneration` (folded into `gridKey`)
   *  so React actually throws away the old MatrixGrid instance and mounts
   *  a fresh one seeded from that server truth. This costs the undo stack,
   *  which is the accepted trade for a rare, deliberate action — see
   *  gridKey's own comment. No client-side cell math is needed at all: the
   *  real material's row simply appears from gridQuery.data.rows like any
   *  other server-loaded row once the refetch lands. */
  async function handleIntentBound() {
    // Called fire-and-forget (`void handleIntentBound()`) from the modal's
    // onBound, since the bind itself already succeeded and toasted by the
    // time this runs — a network hiccup on the REFETCH must not leave the
    // bump silently un-fired. If the refetch fails, still bump
    // bindGeneration in `finally` (remounting with whatever's in the cache
    // is never worse than not remounting at all — the intent row will read
    // wrong until the planner retries, same as before this whole feature
    // existed) and say so, since the bind itself already happened
    // server-side and there is nothing to retry there.
    try {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sales-forecast-grid', rangeFrom, rangeTo] }),
        queryClient.invalidateQueries({ queryKey: ['intent-products'] }),
      ])
    } catch {
      toasts.error('Bind succeeded, but the grid could not refresh automatically — reload the page to see the moved numbers.')
    } finally {
      setBindGeneration((g) => g + 1)
    }
  }

  // BindIntentModal's "this will move N months / X t" preview — computed
  // from `committed` (the last known persisted state), not `liveCells`, so
  // it reflects what's actually on the server and about to move rather
  // than an in-progress unsaved edit. See BindIntentModal.tsx's header for
  // why this is a client-side estimate rather than a server dry-run.
  const bindPreview = useMemo(() => {
    if (!bindTarget) return { months: 0, totalKg: 0 }
    let months = 0
    let totalKg = 0
    for (const [key, qty] of committed) {
      const { rowId } = parseCellKey(key)
      if (rowId === bindTarget.code && qty !== 0) {
        months += 1
        totalKg += qty
      }
    }
    return { months, totalKg }
  }, [bindTarget, committed])

  // Bind must never race autosave (fix round 1, Critical 3): a bind that
  // fires while a debounced PUT /series/cells is still pending, or right
  // after one landed but before its own state settled, can (a) let that
  // PUT's success handler write intent-code keys back into `committed`
  // after the bind already moved them server-side, producing a spurious
  // dirty diff and a ghost autosave, or (b) let a PUT queued before the
  // bind land AFTER it, resurrecting the just-migrated months under the
  // intent code — leaving the same data under both codes on the server.
  // Blocking Bind on any outstanding save (dirty cells not yet flushed, or
  // a flush in flight) sidesteps both: by the time Bind is clickable again,
  // there is nothing left for a stray autosave to race against.
  const bindBlocked = dirtyCells.length > 0 || saveState === 'saving'

  // Derived from displayUnit — purely display/entry, threaded into
  // <MatrixGrid> below. committed/liveCells/dirtyCells/the autosave payload
  // never see these; they stay in kg regardless of what's selected here.
  const gridUnitScale = displayUnit === 't' ? 1000 : 1
  const gridFormatValue = displayUnit === 't' ? formatTonnes : formatQty
  const rowTotalLabel = displayUnit === 't' ? 'Total (t)' : 'Total (kg)'
  const colTotalLabel = displayUnit === 't' ? 'Monthly Sum (t)' : 'Monthly Sum (kg)'

  async function handleGenerateOutlook(anchorMonth: string, horizonMonths: number) {
    setOutlookBusy(true)
    setOutlookError(null)
    try {
      const created = await seriesApi.generateOutlook(anchorMonth, horizonMonths)
      setOutlookOpen(false)
      // Refresh the shared ['forecast-versions'] cache — this page's
      // Outlooks panel AND ProductionPlanPage's forecast-version picker both
      // read that exact key (see outlooksQuery above). Production Plan's
      // <select> otherwise never learns about `created` on its own: the
      // multi-tab keep-alive shell keeps that page's component (and its
      // query) mounted indefinitely once visited, so without an explicit
      // invalidation the newly confirmed outlook would silently never show
      // up in its picker until a hard reload.
      await queryClient.invalidateQueries({ queryKey: ['forecast-versions'] })
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
          {canWriteForecast && (
            <Button type="button" variant="secondary" size="sm" onClick={() => setAddIntentOpen(true)}>
              <Lightbulb className="h-3.5 w-3.5" /> Add intent product
            </Button>
          )}
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium text-neutral-500">Unit</span>
            <div role="group" aria-label="Display unit" className="inline-flex overflow-hidden rounded-lg border border-neutral-200">
              <Button
                type="button"
                size="sm"
                variant={displayUnit === 'kg' ? 'primary' : 'secondary'}
                aria-pressed={displayUnit === 'kg'}
                onClick={() => setDisplayUnit('kg')}
                className="h-11 min-w-11 rounded-none border-0"
              >
                KG
              </Button>
              <Button
                type="button"
                size="sm"
                variant={displayUnit === 't' ? 'primary' : 'secondary'}
                aria-pressed={displayUnit === 't'}
                onClick={() => setDisplayUnit('t')}
                className="h-11 min-w-11 rounded-none border-0 border-l border-neutral-200"
              >
                Tonne
              </Button>
            </div>
          </div>
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
        <div ref={gridSectionRef} className="flex flex-col gap-2">
          {matrixRows.length === 0 && (
            <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              {/* Two distinct empty states share matrixRows.length===0: genuinely
                  no products loaded/added yet (the material master itself is
                  empty for this range) vs. every existing product having been
                  cleared to zero (see matrixRows' row-has-data filter above) —
                  the latter still has real products server-side, so it must
                  NOT tell the planner to paste a table / add products from
                  scratch, which would be misleading. */}
              {(gridQuery.data.rows.length === 0 && addedRows.size === 0)
                ? 'No products yet. Add one above, or paste a full forecast table copied from Excel (a product-code column creates the rows for you — no need to add products by hand first).'
                : 'All products are currently zero for this range. Edit a cell to bring a product back into view.'}
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
            height={gridHeight}
            rowHeaderLabel="Product"
            rowTotalLabel={rowTotalLabel}
            colTotalLabel={colTotalLabel}
            formatValue={gridFormatValue}
            unitScale={gridUnitScale}
            focusRequest={focusRequest}
            onFocusRequestHandled={() => setFocusRequest(null)}
            clearRowId={clearRowId}
            onRowCleared={() => setClearRowId(null)}
            resolveMaterial={canWriteForecast ? resolveMaterial : undefined}
            highlightColIds={highlightColIds}
            tintRowIds={tintRowIds}
            rowBadge={(row) => (
              // Intent is checked FIRST and is exclusive with No BOM: an
              // intent row can never have a BOM (there's no material for a
              // BOM to attach to yet) but that's not the same fact as a
              // real product's BOM not being set up yet — showing "No BOM"
              // here would send a planner looking for a BOM that was never
              // supposed to exist. gridProductCodes already keeps intent
              // codes out of the /boms/exist query entirely (see above),
              // so noBomRowIds.has(row.id) is never true for one anyway —
              // this check is the explicit, can't-regress guarantee.
              isIntentCode(row.id) ? (
                <span title="Planned SKU with no ERP material code yet — recorded here, but never scheduled by MPS until it's bound to a real code.">
                  <Badge variant="warning" className="shrink-0">
                    Intent
                  </Badge>
                </span>
              ) : noBomRowIds.has(row.id) ? (
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
            rowActions={(row) => {
              // Only ever called while !readOnly (MatrixGrid gates
              // rowActions on that itself — see its own prop doc), which
              // here means canWriteForecast, the same permission the Bind
              // endpoint requires — no extra gate needed.
              const showRemove = addedRows.has(row.id) && !persistedAddedCodes.has(row.id)
              const intent = intentByCode.get(row.id)
              if (!showRemove && !intent) return null
              return (
                <span className="flex shrink-0 items-center gap-1.5">
                  {intent && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); if (!bindBlocked) setBindTarget(intent) }}
                      disabled={bindBlocked}
                      aria-label={
                        bindBlocked
                          ? `Bind ${row.label} to a material code — disabled until autosave finishes`
                          : `Bind ${row.label} to a material code`
                      }
                      title={bindBlocked ? 'Waiting for autosave to finish before this can bind' : 'Bind to material code'}
                      className={
                        bindBlocked
                          ? 'shrink-0 cursor-not-allowed text-neutral-200'
                          : 'shrink-0 text-neutral-400 hover:text-primary-600'
                      }
                    >
                      <Link2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {showRemove && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); handleRemoveAddedRow(row.id) }}
                      aria-label={`Remove ${row.label} — not yet saved`}
                      title="Remove — not yet saved"
                      className="shrink-0 text-neutral-300 hover:text-danger-500"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </span>
              )
            }}
          />
        </div>
      )}

      {/* Outlooks panel (follow-up #2) — confirmed ForecastVersion
          snapshots this page (or another planner) has generated, newest
          first, with a read-only viewer per row. See outlooksQuery above for
          why this deliberately shares the ['forecast-versions'] query key
          with ProductionPlanPage's picker. */}
      {canViewOutlooks && (
        <div className="rounded-lg border border-neutral-200 bg-white p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-neutral-900">Outlooks</h2>
            {!outlooksQuery.isLoading && (
              <span className="text-[11px] text-neutral-400">
                {confirmedOutlooks.length} confirmed
              </span>
            )}
          </div>
          {outlooksQuery.isLoading ? (
            <p role="status" className="py-4 text-center text-xs text-neutral-400">Loading outlooks…</p>
          ) : outlooksQuery.isError ? (
            <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
              {errMsg(outlooksQuery.error, 'Could not load outlooks.')}
            </p>
          ) : confirmedOutlooks.length === 0 ? (
            <p className="py-4 text-center text-xs text-neutral-400">
              No outlooks generated yet — use Generate Outlook above to freeze one for Production Plan.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead>
                  <tr className="border-b border-neutral-100 text-left text-[11px] font-semibold text-neutral-500">
                    <th className="px-2 py-1.5">Version</th>
                    <th className="px-2 py-1.5">Anchor</th>
                    <th className="px-2 py-1.5">Horizon</th>
                    <th className="px-2 py-1.5">Created</th>
                    <th className="px-2 py-1.5" />
                  </tr>
                </thead>
                <tbody>
                  {confirmedOutlooks.map((v) => (
                    <tr key={v.id} className="border-b border-neutral-50 last:border-0">
                      <td className="px-2 py-1.5 font-medium text-neutral-800">{v.version_no}</td>
                      <td className="px-2 py-1.5 text-neutral-600">{v.source_anchor_month ?? '—'}</td>
                      <td className="px-2 py-1.5 text-neutral-600">{v.horizon_months} mo</td>
                      <td className="px-2 py-1.5 text-neutral-500">{formatDateTime(v.created_at)}</td>
                      <td className="px-2 py-1.5 text-right">
                        <Button type="button" size="sm" variant="secondary" onClick={() => setViewingVersion(v)}>
                          <Eye className="h-3.5 w-3.5" /> View
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {outlookOpen && (
        <GenerateOutlookModal
          busy={outlookBusy}
          error={outlookError}
          intentCount={intentRowIds.size}
          onClose={() => setOutlookOpen(false)}
          onGenerate={handleGenerateOutlook}
        />
      )}

      {addIntentOpen && (
        <AddIntentProductModal
          onClose={() => setAddIntentOpen(false)}
          onCreated={handleIntentCreated}
          notifySuccess={toasts.success}
          notifyError={toasts.error}
        />
      )}

      {bindTarget && (
        <BindIntentModal
          intent={bindTarget}
          monthsWithData={bindPreview.months}
          totalQtyKg={bindPreview.totalKg}
          onClose={() => setBindTarget(null)}
          onBound={() => { void handleIntentBound() }}
          notifySuccess={toasts.success}
          notifyError={toasts.error}
        />
      )}

      {viewingVersion && (
        <OutlookViewerModal
          version={viewingVersion}
          onClose={() => setViewingVersion(null)}
          formatValue={gridFormatValue}
          unitScale={gridUnitScale}
          rowTotalLabel={rowTotalLabel}
          colTotalLabel={colTotalLabel}
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
