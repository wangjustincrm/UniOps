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
import { AlertCircle, Check, Eye, Lightbulb, Link2, Loader2, Lock, Sparkles, Trash2, X } from 'lucide-react'
import { Button, Badge } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { MatrixGrid, type GridRow as MatrixRow, type GridCol as MatrixCol } from '@/components/MatrixGrid'
import { ConfirmDialog } from '@/components/ConfirmDialog'
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
  // must never land in this set purely for being added.
  //
  // NOTHING EVER ADDS TO THIS SET. That was the whole hole behind MUST FIX
  // 1's second trigger: a row saved during this session kept its X, and the
  // X deletes. `serverKnownRowIds` (below) is now what actually answers
  // "does the server have a record of this row", derived from `committed` +
  // the grid load + the intent-product registry rather than from
  // bookkeeping someone has to remember to write. This set is kept only as
  // a redundant extra `false` in that same condition and as the thing
  // handleConfirmBind cleans up for a bound code; it is deliberately not
  // load-bearing anymore.
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
  // `bindTarget !== null` ALSO forces <MatrixGrid readOnly> below (fix
  // round 2, Critical 3) — it stays non-null through the entire confirm→
  // bind→refetch→remount sequence, not just while the picker is open, so
  // the grid never becomes editable again while a stale, about-to-be-
  // replaced instance is still mounted. See handleConfirmBind.
  const [addIntentOpen, setAddIntentOpen] = useState(false)
  const [bindTarget, setBindTarget] = useState<IntentProduct | null>(null)
  const [dropTarget, setDropTarget] = useState<IntentProduct | null>(null)
  const [dropBusy, setDropBusy] = useState(false)

  async function handleConfirmDrop() {
    if (!dropTarget) return
    setDropBusy(true)
    try {
      await intentApi.drop(dropTarget.id)
      await queryClient.invalidateQueries({ queryKey: ['intent-products'] })
      toasts.success(`Dropped "${dropTarget.name}".`)
      setDropTarget(null)
    } catch (err) {
      // The backend 409s a bound intent product with a human sentence —
      // surfaced verbatim, the same contract bind follows.
      toasts.error(err instanceof ApiError ? err.message : 'Could not drop this planned product.')
    } finally {
      setDropBusy(false)
    }
  }
  const [bindBusy, setBindBusy] = useState(false)
  const [bindError, setBindError] = useState<string | null>(null)
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
  // Cross-task finding 1: set when the post-bind refetch FAILED. The bind
  // itself succeeded server-side (its POST resolved before
  // refetchAndRemount is even called), so the data has already moved — but
  // every client-side map on this page still describes the pre-bind world.
  // Folded into `gridReadOnly` below, permanently, because there is no
  // honest way back from here without a full reload: `committed`/`liveCells`
  // still hold the INTENT- cells the server no longer has, so any edit would
  // autosave them straight back under a placeholder that is now
  // `status='bound'` (re-binding 409s, and MPS skips it via the frozen
  // `is_intent` column) — silent under-scheduling with no route out through
  // the UI. Terminal by construction: nothing clears this flag, and
  // MatrixGrid renders `rowActions` only while `!readOnly`
  // (MatrixGrid.tsx:732), so no further bind can be started from this page.
  const [bindRefreshFailed, setBindRefreshFailed] = useState(false)
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
  // a successful bind (handleConfirmBind below) bumps it on purpose, because
  // that's the only way an already-mounted MatrixGrid ever learns about data
  // that moved server-side. Every other write path on this page (autosave,
  // Add Product/Add intent product, paste) keeps editing the SAME mount.
  const gridKey = `${rangeFrom}::${rangeTo}::${bindGeneration}`
  // Range-only half of the key above — deliberately excludes
  // `bindGeneration`. Used below to gate the addedRows/persistedAddedCodes
  // wipe: a genuine range change makes every session-added row stale (the
  // ORIGINAL reason this block exists), but a bind-driven remount must
  // NOT wipe rows unrelated to the one that was just bound (fix round 2,
  // Important B — a second intent product created this session but not
  // yet typed into lived only in `addedRows`; wiping the whole map on
  // every bind made it vanish with no way to re-add it). The bound code's
  // own now-stale addedRows/persistedAddedCodes/focusRequest entries are
  // cleaned up surgically instead, in handleConfirmBind's success path.
  const rangeKey = `${rangeFrom}::${rangeTo}`

  if (baseline !== syncedBaseline) {
    setSyncedBaseline(baseline)
    setCommitted(baseline)
    setLiveCells(baseline)
    if (!initialized) setInitialized(true)
  }

  if (rangeKey !== syncedGridKeyForAddedRows) {
    setSyncedGridKeyForAddedRows(rangeKey)
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

  // Every row id the SERVER already owns a record for — the union of (a)
  // the rows the grid load returned, (b) every code that has a durably-saved
  // cell (`committed` is updated in place by doFlush after each successful
  // autosave, so this covers rows first saved during THIS session, which
  // gridQuery.data can never learn about — it is fetched once and
  // deliberately never refetched, see its header comment), and (c) every
  // active intent product, whose record lives in `mrp_intent_products` from
  // the moment Create returns 201, independently of whether the sparse
  // `mrp_demand_series` happens to hold a row for it.
  //
  // MUST FIX 1's second trigger: this is what `showRemove` (rowActions
  // below) now tests, replacing `addedRows.has(row.id)` alone.
  // `persistedAddedCodes` was meant to be that test but is never written to
  // by anything (see its own declaration), so the quick-remove X stayed on
  // rows that were already saved — and clicking it blanks the row, which
  // autosaves qty:0, which upsert_cells turns into a DELETE.
  const serverKnownRowIds = useMemo(() => {
    const ids = new Set<string>()
    for (const r of gridQuery.data?.rows ?? []) ids.add(r.material_code)
    for (const key of committed.keys()) ids.add(parseCellKey(key).rowId)
    for (const code of intentByCode.keys()) ids.add(code)
    return ids
  }, [gridQuery.data, committed, intentByCode])

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
    const out: MatrixRow[] = [
      ...visibleServerRows.map((r) => ({
        id: r.material_code,
        label: intentByCode.get(r.material_code)?.name ?? r.name ?? r.material_code,
      })),
      ...extraRows.map((m) => ({
        id: m.code,
        label: intentByCode.has(m.code) ? (intentByCode.get(m.code)?.name ?? m.code) : (m.name ? `${m.code} — ${m.name}` : m.code),
      })),
    ]
    // MUST FIX 1: an intent product's EXISTENCE is owned by
    // `mrp_intent_products`, not by whether `mrp_demand_series` happens to
    // hold a row for it. Both sources above are blind to that:
    // `gridQuery.data.rows` comes from the sparse series table (no row at
    // all for a product with no quantity typed yet — and upsert_cells
    // DELETES on qty==0), while `addedRows` is pure session state a refresh
    // wipes. Create an intent product, type nothing, refresh: the named
    // record the planner deliberately created had no row here, no
    // MaterialPicker entry (that searches mdm master data, which by
    // definition has no intent code) and no drop action — permanently
    // unreachable. Appending every active intent product not already emitted
    // above closes that, and also keeps an intent row visible after its
    // quantities are cleared to zero (which drops it out of
    // `visibleServerRows` while leaving the registry record behind).
    // Only ACTIVE ones: intentApi.list() defaults to `status=active`, so a
    // bound or dropped product is never resurrected here.
    const emitted = new Set(out.map((r) => r.id))
    for (const ip of intentQuery.data ?? []) {
      if (emitted.has(ip.code)) continue
      emitted.add(ip.code)
      out.push({ id: ip.code, label: ip.name })
    }
    return out
  }, [gridQuery.data, addedRows, initialized, matrixCols, liveCells, intentByCode, intentQuery.data])

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
  //
  // Return type is `Promise<boolean>` (fix round 2, Critical 3) — not just
  // fire-and-forget — so handleConfirmBind can force-flush right before a
  // bind and know, authoritatively, whether everything actually landed:
  // `true` covers both "nothing was dirty" and "the save succeeded";
  // `false` is the one case (the PUT itself threw) where cells are still
  // dirty afterward and a bind must not proceed.
  const flushRef = useRef<() => Promise<boolean>>(async () => true)
  const saveTimer = useRef<number | null>(null)
  const savedResetTimer = useRef<number | null>(null)

  const doFlush = async (): Promise<boolean> => {
    const months = gridQuery.data?.months ?? []
    if (months.length === 0) return true
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
    if (cellsToSave.length === 0) return true

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
      return true
    } catch (err) {
      setSaveState('error')
      toasts.error(errMsg(err, 'Autosave failed — please retry.'))
      return false
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

  /** Refetches the grid + intent-products lists and bumps `bindGeneration`
   *  so MatrixGrid remounts off server truth — the client-side half of
   *  "how does an already-mounted MatrixGrid learn a bind moved data
   *  server-side" (see gridKey's comment for the full argument; fix round
   *  1's task-5-report.md has the incident this replaced — an in-place
   *  re-key that changed parent state MatrixGrid never re-reads).
   *
   *  Fix round 2, Important A: `queryClient.invalidateQueries` swallows a
   *  failed refetch into a no-op resolved promise UNLESS `throwOnError` is
   *  passed (see query-core's `refetchQueries`: `if (!fetchOptions.
   *  throwOnError) { promise = promise.catch(noop) }`) — the `try/catch`
   *  below only does anything because `throwOnError: true` is passed on
   *  the grid invalidation. Fix round 1's report claimed the catch already
   *  worked; it did not, because that argument was never checked against
   *  invalidateQueries' actual default. This is the corrected version. */
  async function refetchAndRemount() {
    try {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sales-forecast-grid', rangeFrom, rangeTo] }, { throwOnError: true }),
        queryClient.invalidateQueries({ queryKey: ['intent-products'] }, { throwOnError: true }),
      ])
    } catch {
      // Cross-task finding 1. A toast alone was not enough: `finally` still
      // bumps `bindGeneration`, and handleConfirmBind then clears
      // `bindTarget`, which used to release `readOnly` and hand a live,
      // EDITABLE grid back — remounted from the stale `liveCells`, which
      // still carries the INTENT- rows the server has already moved away.
      // Typing there autosaves series rows back under a now-`bound`
      // placeholder. This flag keeps the grid read-only for the rest of the
      // page's life (see gridReadOnly), and drives a standing banner rather
      // than a toast that scrolls away.
      setBindRefreshFailed(true)
      toasts.error('Bind succeeded, but the grid could not refresh automatically — reload the page to see the moved numbers.')
    } finally {
      setBindGeneration((g) => g + 1)
    }
  }

  /** Confirm handler for BindIntentModal — owns the ENTIRE bind sequence
   *  (guard, the actual POST, and the post-bind refetch+remount), not just
   *  the network call, because the guard needs page-level state
   *  (dirtyCells/saveTimer/flushRef) BindIntentModal itself never had.
   *
   *  Fix round 2, Critical 3 (two layers, per the review — either alone
   *  left a hole):
   *
   *  Layer 1 lives where <MatrixGrid readOnly={...}> is rendered below:
   *  `bindTarget !== null` forces `readOnly`, for the WHOLE time this
   *  modal is open — not just at the moment the row's Bind icon was
   *  clicked. That single prop closes every window-level listener
   *  MatrixGrid registers regardless of what's visually covered by the
   *  modal's backdrop: the paste handler's very first line is `if
   *  (readOnly) return` (MatrixGrid.tsx, the `paste` effect), the undo/
   *  redo branch checks `if (readOnly || locked) return` (the `keydown`
   *  effect) — neither is gated on `data-matrix-input` the way Ctrl+C is,
   *  which is exactly why Ctrl+Z used to reach past the modal. readOnly
   *  also collapses every cell to the non-editable `<td>` branch in
   *  MatrixCell (`nonEditable = readOnly || frozen`), which has no
   *  `<input>` at all — so there is no DOM node left for a stray keystroke
   *  to land in, and any not-yet-committed edit still sitting in a cell's
   *  local buffer at the moment readOnly flips is simply dropped (never
   *  reaches onCommit), not silently applied later.
   *
   *  Layer 2 is this function, right here, right before the fetch: cancel
   *  any still-pending debounce timer, force a flush, and only proceed if
   *  that flush actually left nothing dirty. This is not redundant with
   *  Layer 1 — it is what happens if a save was ALREADY in flight (started
   *  before bindTarget was set) or already dirty at the moment the Bind
   *  icon was clicked, neither of which Layer 1 can undo after the fact,
   *  since Layer 1 only stops FUTURE edits/paste/undo, it does not cancel
   *  or await something already queued. */
  async function handleConfirmBind(materialCode: string) {
    if (!bindTarget) return
    setBindBusy(true)
    setBindError(null)
    try {
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current)
        saveTimer.current = null
      }
      // flushRef.current() is doFlush's own return value — `true` means
      // either there was nothing to save or the save just succeeded (in
      // which case `committed` was updated to match `liveCells` for every
      // cell it just saved), `false` means the PUT itself threw and cells
      // are still dirty. Checking `dirtyCells.length` on top of `flushed`
      // is not stale-closure guesswork here: Layer 1 has already forced
      // the grid readOnly (bindTarget is non-null for this whole call), so
      // nothing NEW can have become dirty since this function started —
      // `dirtyCells` can only still be non-empty here because the flush
      // itself failed, which `flushed` already told us.
      const flushed = await flushRef.current()
      if (!flushed || dirtyCells.length > 0) {
        setBindError('There are unsaved changes on the grid — resolve them, then try binding again.')
        return
      }
      const result = await intentApi.bind(bindTarget.id, materialCode)
      // The outlook caveat is not a nicety: MPS runs off an immutable
      // ForecastVersion snapshot, and every snapshot frozen BEFORE this bind
      // still carries this product's lines with `is_intent = true`, which
      // _load_intent_lines / _build_demand_items exclude from scheduling
      // (mrp-api/app/api/v1/mps.py). Correct by design — snapshots do not
      // change retroactively — but a planner who binds and then re-runs the
      // existing outlook sees the product skipped again and reads that as
      // the bind not having worked.
      toasts.success(
        `Bound ${bindTarget.name} to ${materialCode} — moved ${result.moved_months} month(s), `
        + `${formatTonnes(Number(result.moved_qty))} t. Existing outlooks still exclude it: `
        + 'generate a new outlook before MPS will schedule this product.',
      )
      const boundCode = bindTarget.code
      // Keep bindTarget set (grid stays readOnly) through the refetch +
      // remount too — clearing it here, before the remount lands, would
      // hand editing back to the OLD MatrixGrid instance, which is about
      // to be discarded, letting a keystroke land on cells that vanish the
      // instant bindGeneration bumps.
      await refetchAndRemount()
      // Fix round 2, Important B: drop just the bound code's now-stale
      // session bookkeeping — it has zero cells left (bind moved them all
      // away) and would otherwise ghost-render as an extra row forever
      // (matrixRows' extraRows filter only excludes an addedRows entry
      // whose code matches a CURRENT server row, and the bound code has
      // none anymore post-bind). Everything else in addedRows/
      // persistedAddedCodes survives this remount untouched — a second,
      // untouched intent product created this same session must not
      // disappear just because a DIFFERENT one got bound.
      setAddedRows((prev) => {
        if (!prev.has(boundCode)) return prev
        const next = new Map(prev)
        next.delete(boundCode)
        return next
      })
      setPersistedAddedCodes((prev) => {
        if (!prev.has(boundCode)) return prev
        const next = new Set(prev)
        next.delete(boundCode)
        return next
      })
      setFocusRequest((f) => (f?.rowId === boundCode ? null : f))
      setBindTarget(null)
    } catch (err) {
      // Verbatim: the 409 body here is exactly what decision D11 wants a
      // human to read (e.g. "<code> already has forecast rows — merge them
      // by hand first") — errMsg/ApiError already carry FastAPI's `detail`
      // through as-is (see lib/api.ts's detailToMessage), so this is not a
      // generic "bind failed" fallback overwriting it.
      setBindError(errMsg(err, 'Could not bind this intent product — please retry.'))
    } finally {
      setBindBusy(false)
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

  // UX affordance only — NOT the correctness guarantee. Fix round 1
  // claimed this button-disabled check alone was sufficient to keep Bind
  // from racing autosave; fix round 2's review showed that was false (it
  // reasoned about mouse reachability, not about MatrixGrid's own window
  // `keydown`/`paste` listeners, which are not gated on the modal's visual
  // backdrop at all). The actual guarantee now lives in two other places:
  // <MatrixGrid readOnly={...}> below (forced true for the whole time
  // bindTarget is set, not just at this instant) and handleConfirmBind's
  // own cancel-timer/flush/re-check sequence right before the POST. This
  // constant only decides whether the row's Bind icon LOOKS clickable —
  // disabling it here is still worth doing so a planner doesn't open the
  // picker only to have handleConfirmBind immediately reject it, but a
  // stale read of this value can never be the thing standing between a
  // click and a race, because nothing downstream trusts it anymore.
  const bindBlocked = dirtyCells.length > 0 || saveState === 'saving'

  // Fix round 2, Critical 3, Layer 1 — forced true for the ENTIRE time
  // BindIntentModal is open (bindTarget non-null through confirm→bind→
  // refetch→remount, not just while the picker is open; see
  // handleConfirmBind). readOnly is not cosmetic here: MatrixGrid's paste
  // effect starts with `if (readOnly) return` and its undo/redo keydown
  // branch checks `if (readOnly || locked) return` — both are WINDOW
  // listeners, reachable regardless of what the modal's backdrop visually
  // covers, and neither is gated on focus being inside the grid (unlike
  // Ctrl+C, which does check `data-matrix-input`). readOnly also collapses
  // every MatrixCell to the non-editable `<td>` branch, so there is no
  // `<input>` left for a stray keystroke to land in at all.
  // `bindRefreshFailed` is cross-task finding 1's half: once a post-bind
  // refetch has failed, this page's client state provably disagrees with the
  // server and cannot be reconciled without a reload, so editing stays shut
  // for good (see that state's own declaration for the full argument).
  const gridReadOnly = !canWriteForecast || bindTarget !== null || bindRefreshFailed

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

      {/* Cross-task finding 1 — persistent, not a toast: the bind DID land
          server-side, but this page's numbers are stale and editing is now
          locked (gridReadOnly), so the planner needs a standing explanation
          plus the one action that resolves it. */}
      {bindRefreshFailed && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          <span>
            The bind was saved, but this page could not reload the forecast afterwards — the
            numbers below are out of date, so editing is locked. Reload to continue.
          </span>
          <Button type="button" size="sm" variant="secondary" onClick={() => window.location.reload()}>
            Reload page
          </Button>
        </div>
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
            readOnly={gridReadOnly}
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
              // rowActions on that itself — see its own prop doc). Since
              // `readOnly={gridReadOnly}` above is also true for the
              // entire time a bind is in flight, every row action
              // (including every OTHER intent row's own Bind icon)
              // disappears while BindIntentModal is open — consistent
              // with nothing else on the grid being actionable then.
              // MUST FIX 1, second trigger. The X clears the row, which
              // autosaves qty:0, which upsert_cells turns into a DELETE — so
              // it must appear ONLY for a row the server has no record of.
              // `addedRows.has()` alone did not say that (the map keeps a
              // code for the whole session, saved or not) and
              // `persistedAddedCodes` — the set that was meant to say it —
              // is never written to by anything. `serverKnownRowIds` (see
              // above) is the real test: a durably-saved cell OR an
              // intent-product record. For an intent row it is true from the
              // instant Create returns 201, which is the case that matters:
              // an ordinary product could at least be re-picked from master
              // data, an intent product could not be recovered at all.
              const showRemove = addedRows.has(row.id)
                && !persistedAddedCodes.has(row.id)
                && !serverKnownRowIds.has(row.id)
              const intent = intentByCode.get(row.id)
              if (!showRemove && !intent) return null
              return (
                <span className="flex shrink-0 items-center gap-1.5">
                  {intent && (
                    // A `title` on a DISABLED <button> is not reliably
                    // rendered by every browser (fix round 2) — the same
                    // problem the "No BOM"/"Intent" Badge tooltip above
                    // already works around, by hanging the tooltip off a
                    // plain, never-disabled wrapping <span> instead.
                    <span title={bindBlocked ? 'Waiting for autosave to finish before this can bind' : 'Bind to material code'}>
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); if (!bindBlocked) setBindTarget(intent) }}
                        disabled={bindBlocked}
                        aria-label={
                          bindBlocked
                            ? `Bind ${row.label} to a material code — disabled until autosave finishes`
                            : `Bind ${row.label} to a material code`
                        }
                        // Disabled must read as LESS prominent than
                        // enabled, and still stay legible. Fix round 2
                        // over-corrected `text-neutral-200` (nearly
                        // invisible) all the way to `text-neutral-500`,
                        // which is DARKER than the enabled `text-neutral-400`
                        // — the affordance read backwards. neutral-300 is one
                        // step lighter than enabled and is the tone the
                        // remove-X below already rests at, so it is a value
                        // this grid is known to render legibly.
                        className={
                          bindBlocked
                            ? 'shrink-0 cursor-not-allowed text-neutral-300'
                            : 'shrink-0 text-neutral-400 hover:text-primary-600'
                        }
                      >
                        <Link2 className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  )}
                  {intent && (
                    // Until now the backend had a drop endpoint with no
                    // caller at all, so an intent product created under the
                    // wrong name stayed on this grid forever. Same
                    // disabled-button tooltip workaround as Bind above.
                    <span title={bindBlocked ? 'Waiting for autosave to finish' : 'Drop this planned product'}>
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); if (!bindBlocked) setDropTarget(intent) }}
                        disabled={bindBlocked}
                        aria-label={`Drop ${row.label} — a planned product that was never bound`}
                        className={
                          bindBlocked
                            ? 'shrink-0 cursor-not-allowed text-neutral-300'
                            : 'shrink-0 text-neutral-400 hover:text-danger-600'
                        }
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </span>
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
          busy={bindBusy}
          error={bindError}
          onClose={() => { setBindTarget(null); setBindError(null) }}
          onConfirm={(materialCode) => { void handleConfirmBind(materialCode) }}
        />
      )}

      {dropTarget && (
        <ConfirmDialog
          title={`Drop "${dropTarget.name}"?`}
          confirmLabel="Drop"
          danger
          busy={dropBusy}
          onCancel={() => setDropTarget(null)}
          onConfirm={() => { void handleConfirmDrop() }}
        >
          This planned product was never bound to a material code. Its forecast quantities
          stay in the grid until you clear them — dropping only retires the placeholder so
          it stops being offered.
        </ConfirmDialog>
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
