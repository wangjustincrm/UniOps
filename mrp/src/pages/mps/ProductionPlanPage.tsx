// Production Plan / MPS — design §6.6 page 3, Phase 1B Task 6. The most
// valuable screen of the feature: generate a Master Production Schedule off
// a confirmed forecast version, see per-month capacity occupancy, review/
// adjust individual plan lines, and Confirm & Release into mrp_demands
// (Phase 1C's material requirements calc consumes that table next).
//
// Backend is mrp-api's /mps/* (app/api/v1/mps.py, Task 4) — see mpsApi.ts's
// header for the endpoint contracts and permission keys. Not wired into
// nav/routes here — that's Task 7.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Sparkles, AlertTriangle, Download, PackageCheck, X as XIcon } from 'lucide-react'
import { Button, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { ToastStack } from '@/components/Toast'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { StatusBadge } from '@/components/StatusBadge'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { materialsApi, type MaterialOption } from '@/lib/materials'
import { forecastApi, saveBlob } from '@/pages/forecast/forecastApi'
import { bomStatusApi } from '@/pages/forecast/bomStatusApi'
import { capacityApi, findExistingException } from '@/pages/capacity/capacityApi'
import { closesWeek, findSkuClosure } from '@/pages/capacity/closedWeek'
import { mpsApi, type MpsLine, type WeekGridEntry } from './mpsApi'
import { ProductionMatrix } from './ProductionMatrix'
import { AdjustDrawer } from './AdjustDrawer'
import { WeekDrawer } from './WeekDrawer'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

const EMPTY_STRING_SET: ReadonlySet<string> = new Set()

// Display-only unit toggle for the matrix — the stored/planning unit is
// always kg (capacity/MPS math depends on it); this only scales what's
// shown. Same convention as SalesForecastPage's DisplayUnit/localStorage
// pair, distinct storage key per page.
type DisplayUnit = 'kg' | 't'
const DISPLAY_UNIT_STORAGE_KEY = 'mrp.plan.displayUnit'

function loadDisplayUnit(): DisplayUnit {
  try {
    const v = window.localStorage.getItem(DISPLAY_UNIT_STORAGE_KEY)
    return v === 'kg' || v === 't' ? v : 't'
  } catch {
    return 't' // localStorage unavailable (e.g. privacy mode) — fall back to the default
  }
}

/** A single ProductionMatrix Planned cell can aggregate more than one
 *  MpsLine (e.g. two different demand_months pre-built into the same
 *  plan week, or several weeks rolled into one collapsed month's summary
 *  column) — see ProductionMatrix.tsx's `onAdjustCell` contract and Task
 *  4's report. This picker lets the planner disambiguate which of the
 *  underlying lines they meant to adjust before the single-line
 *  AdjustDrawer opens. */
function AdjustCellPicker({
  lines, materialsByCode, formatValue, onPick, onClose,
}: {
  lines: MpsLine[]
  materialsByCode: Map<string, MaterialOption>
  formatValue: (kg: number) => string
  onPick: (line: MpsLine) => void
  onClose: () => void
}) {
  const code = lines[0]?.material_code ?? ''
  const name = materialsByCode.get(code)?.name

  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-label={`Select a line to adjust for ${code}`} className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-neutral-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-neutral-900">Select a line</h2>
            <p className="font-mono text-xs text-neutral-500">{code}{name && name !== code ? ` · ${name}` : ''}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-neutral-600"
          >
            <XIcon className="h-5 w-5" />
          </button>
        </div>
        <div className="max-h-80 overflow-y-auto px-2 py-2">
          {lines.map((line) => (
            <button
              key={line.id}
              type="button"
              onClick={() => onPick(line)}
              className="flex min-h-[44px] w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm hover:bg-primary-50 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <span className="text-neutral-600">demand {line.demand_month}</span>
              <span className="font-mono text-neutral-900">{formatValue(Number(line.qty))}</span>
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  )
}

export default function ProductionPlanPage() {
  const queryClient = useQueryClient()
  const toasts = useToasts()
  const permsQuery = usePermissions()
  const canExecute = !!permsQuery.data?.permissions['mrp.run.execute']
  const canRelease = !!permsQuery.data?.permissions['mrp.proposal.confirm']
  const canView = !!permsQuery.data?.permissions['mrp.report.view']
  // Gates WeekDrawer's checkbox (same key capacity/*.tsx uses for rule/
  // exception writes) — deliberately independent of canExecute/canRelease:
  // marking a maintenance week is a capacity-parameter change, not a
  // run-execution or release action.
  const canWriteParams = !!permsQuery.data?.permissions['mrp.param.write']

  // ── Display unit (kg/tonne) ─────────────────────────────────────────────
  const [displayUnit, setDisplayUnit] = useState<DisplayUnit>(() => loadDisplayUnit())
  useEffect(() => {
    try {
      window.localStorage.setItem(DISPLAY_UNIT_STORAGE_KEY, displayUnit)
    } catch {
      // localStorage unavailable — display-only preference, safe to drop
    }
  }, [displayUnit])
  function formatValue(kg: number): string {
    const n = displayUnit === 't' ? kg / 1000 : kg
    return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
  }

  // ── Confirmed forecast versions (Generate can only run off one — mps.py's
  // create_run() 409s otherwise, so the picker only ever offers confirmed
  // ones rather than showing a choice that would just fail). ──────────────
  const versionsQuery = useQuery({
    queryKey: ['forecast-versions'],
    queryFn: () => forecastApi.listVersions(1, 100),
  })
  const confirmedVersions = useMemo(
    () => (versionsQuery.data?.items ?? []).filter((v) => v.status === 'confirmed'),
    [versionsQuery.data],
  )
  const [manualVersionId, setManualVersionId] = useState<string | null>(null)
  const selectedVersionId = manualVersionId ?? confirmedVersions[0]?.id ?? null

  // The single most-recently-confirmed outlook among the (now possibly
  // several) coexisting confirmed ForecastVersions — Continuous Sales
  // Forecast Task 4 dropped the old supersede-on-confirm rule, so more than
  // one can be 'confirmed' at once. Ties (identical confirmed_at) resolve
  // to whichever is encountered last; versions with a null confirmed_at
  // (shouldn't happen for status='confirmed', but be defensive) never win.
  const latestConfirmedVersion = useMemo(() => {
    let latest: (typeof confirmedVersions)[number] | null = null
    for (const v of confirmedVersions) {
      if (!v.confirmed_at) continue
      if (!latest?.confirmed_at || new Date(v.confirmed_at) >= new Date(latest.confirmed_at)) {
        latest = v
      }
    }
    return latest
  }, [confirmedVersions])

  // ── The active run ───────────────────────────────────────────────────────
  const [runId, setRunId] = useState<string | null>(null)

  const runQuery = useQuery({
    queryKey: ['mps-run', runId],
    queryFn: () => mpsApi.get(runId as string),
    enabled: !!runId,
  })
  const run = runQuery.data ?? null
  const isReleased = run?.status === 'released'

  // Which confirmed outlook this run was generated from (Task 10, design
  // §8 "one active released plan"): confirmedVersions is the same list the
  // "Forecast Version" picker above draws from, so a run's
  // forecast_version_id should always resolve here — but if it doesn't
  // (e.g. list still loading, or some future path confirms off a
  // non-confirmed version) fall back to null and treat that as non-latest
  // rather than silently skipping the warning.
  const runVersion = useMemo(
    () => confirmedVersions.find((v) => v.id === run?.forecast_version_id) ?? null,
    [confirmedVersions, run],
  )
  const isLatestOutlook = !!run && !!latestConfirmedVersion && run.forecast_version_id === latestConfirmedVersion.id

  function invalidateRun() {
    return queryClient.invalidateQueries({ queryKey: ['mps-run', runId] })
  }

  // Materials master, for the table's Product column (name is not part of
  // MpsLineResponse — see mpsApi.ts). Same fetch-whole-master-once pattern
  // as ForecastPage.tsx/BomExplorerPage.tsx (materialsApi.listAll() pages
  // through mdm-api's ~2600-row master once, cached 5 minutes).
  const materialsAllQuery = useQuery({
    queryKey: ['mps-materials-all'],
    queryFn: () => materialsApi.listAll(),
    enabled: !!runId,
    staleTime: 5 * 60_000,
  })
  const materialsByCode = useMemo(() => {
    const map = new Map<string, MaterialOption>()
    for (const m of materialsAllQuery.data ?? []) map.set(m.code, m)
    return map
  }, [materialsAllQuery.data])

  // No-BOM flagging (Continuous Sales Forecast Task 9, spec §8b): 1B never
  // explodes BOMs, so this is display-only — but a planner reviewing a run
  // should still see which lines have no established formulation yet.
  // Same batch source (mdm-api's /boms/exist) the Sales Forecast grid uses —
  // see bomStatusApi.ts — so "has a BOM" is defined in exactly one place.
  const runProductCodes = useMemo(
    () => [...new Set((run?.lines ?? []).map((l) => l.material_code))].sort(),
    [run],
  )
  const bomStatusQuery = useQuery({
    queryKey: ['mps-bom-status', runProductCodes],
    queryFn: () => bomStatusApi.withBom(runProductCodes),
    enabled: runProductCodes.length > 0,
    staleTime: 5 * 60_000,
  })
  const noBomCodes = useMemo(() => {
    const withBom = bomStatusQuery.data
    if (!withBom) return EMPTY_STRING_SET
    const s = new Set<string>()
    for (const code of runProductCodes) if (!withBom.has(code)) s.add(code)
    return s
  }, [runProductCodes, bomStatusQuery.data])

  // Capacity exceptions — double duty since round 1's review fix:
  //  (1) WeekDrawer's source for "does this week already have a maintenance
  //      exception, and is it active" (its own read-before-write upsert
  //      rule, see capacityApi.ts's `findExistingException` doc: at most
  //      one exception row can ever exist per week+scope+constraint, so
  //      re-marking a week must PATCH that row, never POST a second one).
  //  (2) ProductionMatrix's grey-tint/wrench signal. Round 1: this used to
  //      read `run.capacity_occupancy` instead — WRONG, because occupancy
  //      is built from the run's own LINES (mrp-api's
  //      `_compute_capacity_occupancy`) and a maintenance week has ZERO
  //      lines by construction (`_week_can_host` closes it to all
  //      placement) — so the tint vanished the instant a planner did the
  //      one thing the WeekDrawer save toast tells them to do
  //      (Recalculate), and stayed gone on reload. Exceptions describe what
  //      is CONFIGURED, not what got PLACED, so they don't share that blind
  //      spot. (The alternative — have the backend emit an occupancy row
  //      for every week_grid entry regardless of lines — would work too,
  //      but reshapes a response several other things already read
  //      ["how full is this week"] for a display concern this file alone
  //      has; reusing the query already on this page is the smaller,
  //      better-scoped fix.)
  const exceptionsQuery = useQuery({
    queryKey: ['capacity-exceptions'],
    queryFn: () => capacityApi.listExceptions(),
    enabled: !!runId,
  })

  function invalidateExceptions() {
    return queryClient.invalidateQueries({ queryKey: ['capacity-exceptions'] })
  }

  // A CLOSED week — one the engine will not place anything into — is
  // tinted. The predicate is `capacityApi.closesWeek`, restated from
  // `mps_engine.py::_week_can_host`, so the tint means exactly what the
  // engine does: `max_output_qty <= 0` OR `max_sku_count < 1`, factory
  // scope, active.
  //
  // This used to test only `max_output_qty === 0`. `Max SKUs / week` is
  // offered in the Week Exceptions dropdown and its value field accepts 0,
  // so a week closed that way was fully honoured by the engine, never
  // tinted here, and then offered to the planner by WeekDrawer as an
  // unmarked week it could "mark as maintenance" — a second exception row
  // for the same week (different constraint_type, so the partial unique
  // index allows it) saying the same thing twice.
  //
  // A non-zero active max_output_qty override (a legitimate de-rate, not a
  // shutdown) is still deliberately NOT tinted — the week can host
  // production, just less of it. See WeekDrawer.tsx's round-1 finding #2.
  const maintenanceWeekStarts = useMemo(() => {
    const s = new Set<string>()
    for (const e of exceptionsQuery.data ?? []) {
      if (closesWeek(e)) s.add(e.week_start)
    }
    return s
  }, [exceptionsQuery.data])

  // ── Generate / Recalculate ──────────────────────────────────────────────
  const [generating, setGenerating] = useState(false)
  const [recalculating, setRecalculating] = useState(false)
  // Production Lead Time (mrp08, now WEEKS since the weekly rework): how
  // many weeks earlier than a demand month's last week the engine should
  // try to schedule production — default 4 matches the backend's own
  // default (mps.py's DEFAULT_PRODUCTION_LEAD_WEEKS) when omitted.
  const [leadWeeks, setLeadWeeks] = useState(4)

  async function handleGenerate() {
    if (!selectedVersionId) return
    setGenerating(true)
    try {
      const result = await mpsApi.generate(selectedVersionId, { production_lead_weeks: leadWeeks })
      setRunId(result.id)
      toasts.success(`Generated ${result.run_no} — ${result.lines.length} line(s).`)
    } catch (err) {
      toasts.error(errMsg(err, 'Could not generate the MPS run — please retry.'))
    } finally {
      setGenerating(false)
    }
  }

  async function handleRecalculate() {
    if (!runId) return
    setRecalculating(true)
    try {
      const result = await mpsApi.recalculate(runId)
      toasts.success(`Recalculated ${result.run_no} — ${result.lines.length} line(s).`)
      await invalidateRun()
    } catch (err) {
      toasts.error(errMsg(err, 'Could not recalculate this run — please retry.'))
    } finally {
      setRecalculating(false)
    }
  }

  // ── Adjust one line (from a matrix Planned cell) ────────────────────────
  // A cell can aggregate more than one MpsLine (ProductionMatrix.tsx's
  // onAdjustCell contract) — a single-line cell goes straight to the
  // drawer; a multi-line cell opens AdjustCellPicker first so the planner
  // disambiguates. Locking a line is now done from inside AdjustDrawer
  // itself (a `locked_by_planner` checkbox) rather than a bulk table
  // selection — MpsLineTable's row-checkbox Lock/Unlock action bar is
  // retired along with the table.
  const [adjustTarget, setAdjustTarget] = useState<MpsLine | null>(null)
  const [pickerLines, setPickerLines] = useState<MpsLine[] | null>(null)

  function handleAdjustCell(lines: MpsLine[]) {
    if (lines.length === 0) return
    if (lines.length === 1) {
      setAdjustTarget(lines[0])
      return
    }
    setPickerLines(lines)
  }

  // ── Mark a week (from a matrix week header) ─────────────────────────────
  // design §5.1's last bullet — same click-to-open interaction as
  // handleAdjustCell above, just keyed off a week column instead of a
  // Planned cell. See WeekDrawer.tsx's header comment for why this never
  // calls handleRecalculate directly: it's only ever offered via the
  // success toast's action button, so a hand adjustment elsewhere in this
  // run doesn't get silently swept away by a recalculate the planner didn't
  // ask for.
  const [weekDrawerTarget, setWeekDrawerTarget] = useState<WeekGridEntry | null>(null)

  // ── Export ───────────────────────────────────────────────────────────────
  const [exporting, setExporting] = useState(false)

  async function handleExport() {
    if (!runId) return
    setExporting(true)
    try {
      const blob = await mpsApi.exportRun(runId, displayUnit)
      saveBlob(blob, null, 'production-plan.xlsx')
      toasts.success('Production plan exported.')
    } catch (err) {
      toasts.error(errMsg(err, 'Could not export this run — please retry.'))
    } finally {
      setExporting(false)
    }
  }

  // ── Confirm & Release ────────────────────────────────────────────────────
  const [releaseConfirmOpen, setReleaseConfirmOpen] = useState(false)
  const [releasing, setReleasing] = useState(false)

  // Intent-product rows (planned SKUs with no ERP material code yet) this
  // run's generate/recalculate skipped entirely — see mpsApi.ts's
  // MpsSkippedIntent. mrp-api builds `stats.skipped_intent` as one entry
  // PER mrp_forecast_lines row (unique on version_id + material_code +
  // month, see mps.py's _skipped_intent_stats) — i.e. one intent PRODUCT
  // with 18 forecast months produces 18 entries, all with the same code
  // and name. Aggregated here by `code` (summing qty, keeping any one
  // name) so the count/list below describe products, not product-months —
  // the raw per-line array would report "18 intent products" for one and
  // repeat its name 18 times with no indication that's what happened.
  const skippedIntent = useMemo(() => {
    const raw = run?.stats?.skipped_intent ?? []
    const byCode = new Map<string, { code: string; name: string; namedFromLine: boolean; qty: number }>()
    for (const item of raw) {
      const qty = Number(item.qty)
      const existing = byCode.get(item.code)
      // `item.name` is nullable on the wire (mpsApi.ts's MpsSkippedIntent —
      // it is ForecastLine.intent_name, a nullable column, carried inside an
      // unvalidated JSONB `stats` blob). Falling back to the placeholder
      // code keeps the callout identifying SOMETHING; a bare null would have
      // rendered as "Not scheduled (intent):  · 700 kg". Also prefers a
      // non-null name from any later line of the same product over an
      // earlier null one.
      if (existing) {
        existing.qty += qty
        if (!existing.namedFromLine && item.name) {
          existing.name = item.name
          existing.namedFromLine = true
        }
      } else {
        byCode.set(item.code, {
          code: item.code,
          name: item.name ?? item.code,
          namedFromLine: !!item.name,
          qty,
        })
      }
    }
    return [...byCode.values()]
  }, [run])

  const releaseSummary = useMemo(() => {
    if (!run) return null
    const releasable = run.lines.filter((l) => !l.capacity_gap)
    const productCount = new Set(releasable.map((l) => l.material_code)).size
    const totalQty = releasable.reduce((sum, l) => sum + Number(l.qty), 0)
    const prebuildCount = run.stats?.prebuild_count ?? run.lines.filter((l) => l.is_prebuild).length
    const gapCount = run.stats?.capacity_gap_count ?? run.lines.filter((l) => l.capacity_gap).length
    return { productCount, totalQty, prebuildCount, gapCount }
  }, [run])

  async function handleConfirmRelease() {
    if (!runId) return
    setReleasing(true)
    try {
      const result = await mpsApi.confirmRelease(runId)
      toasts.success(`Released ${result.run_no} — demand written to the MRP requirements table.`)
      setReleaseConfirmOpen(false)
      await invalidateRun()
    } catch (err) {
      toasts.error(errMsg(err, 'Could not release this run — please retry.'))
    } finally {
      setReleasing(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-neutral-900">Production Plan</h1>
        {run && (
          <div className="flex items-center gap-2 text-sm text-neutral-600">
            <span className="font-mono text-xs text-neutral-500">{run.run_no}</span>
            <StatusBadge status={run.status} />
            {runVersion && (
              <span className="text-xs text-neutral-500">
                from {runVersion.version_no} · anchor {runVersion.source_anchor_month ?? '—'}
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Toolbar: version picker + unit toggle + Generate/Recalculate/Export/Release ── */}
      <div className="flex flex-wrap items-end justify-between gap-3 rounded-lg border border-neutral-200 bg-white p-3">
        <div className="flex flex-wrap items-end gap-3">
          <FormField
            label="Forecast Version"
            htmlFor="mps-version"
            hint={confirmedVersions.length === 0 ? 'No confirmed forecast version available — confirm one first.' : undefined}
          >
            <select
              id="mps-version"
              value={selectedVersionId ?? ''}
              onChange={(e) => setManualVersionId(e.target.value || null)}
              disabled={confirmedVersions.length === 0 || generating}
              className="flex h-10 min-w-[220px] rounded-lg border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {confirmedVersions.length === 0 && <option value="">No confirmed versions</option>}
              {confirmedVersions.map((v) => (
                <option key={v.id} value={v.id}>{v.version_no} · {v.horizon_start_month} · {v.horizon_months}mo</option>
              ))}
            </select>
          </FormField>

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

        <div className="flex flex-wrap items-center gap-2">
          {canExecute && (
            <>
              <FormField label="Lead (weeks)" htmlFor="mps-lead-weeks">
                <input
                  id="mps-lead-weeks"
                  type="number"
                  min={0}
                  max={52}
                  value={leadWeeks}
                  onChange={(e) => {
                    const n = Number(e.target.value)
                    setLeadWeeks(Number.isFinite(n) ? Math.min(52, Math.max(0, Math.trunc(n))) : 0)
                  }}
                  disabled={generating}
                  title="Applies when you Generate a new run; Recalculate keeps the run's lead."
                  className="flex h-11 w-20 rounded-lg border border-neutral-200 bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-60"
                />
              </FormField>
              <Button
                type="button" size="sm" className="min-h-[44px]"
                onClick={handleGenerate}
                disabled={!selectedVersionId || generating}
              >
                {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                Generate
              </Button>
              <Button
                type="button" variant="secondary" size="sm" className="min-h-[44px]"
                onClick={handleRecalculate}
                disabled={!run || isReleased || recalculating}
              >
                {recalculating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Recalculate
              </Button>
            </>
          )}
          {run && canView && (
            <Button
              type="button" variant="secondary" size="sm" className="min-h-[44px]"
              onClick={handleExport}
              disabled={exporting}
            >
              {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Export
            </Button>
          )}
          {run && !isReleased && canRelease && (
            <Button
              type="button" size="sm" className="min-h-[44px]"
              onClick={() => setReleaseConfirmOpen(true)}
              disabled={recalculating || releasing}
            >
              <PackageCheck className="h-3.5 w-3.5" />
              Confirm &amp; Release
            </Button>
          )}
        </div>
      </div>

      {isReleased && (
        <p role="status" className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-600">
          This run is released and immutable — lines can no longer be locked, adjusted, or recalculated.
        </p>
      )}

      {!runId && (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-16 text-center">
          <p className="text-sm text-neutral-500">Pick a confirmed forecast version and Generate to produce a schedule.</p>
        </div>
      )}

      {runQuery.isError && (
        <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {errMsg(runQuery.error, 'Could not load this MPS run.')}
        </p>
      )}

      {runId && runQuery.isLoading && (
        <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-neutral-400">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading run…
        </p>
      )}

      {/* A generated run with zero lines is not an error and not a capacity-rule
          problem: it means every forecast month's net requirement is zero
          (available stock already covers the forecast), so there is nothing to
          schedule. Without an explicit callout the planner sees only the
          generic "no capacity usage" empty state and reasonably assumes their
          rules didn't apply — so say why plainly and skip the bars/table. */}
      {run && run.lines.length === 0 && (
        <div role="status" className="flex flex-col items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 py-14 text-center">
          <p className="text-sm font-medium text-primary-900">No production needed for this forecast.</p>
          <p className="max-w-xl px-4 text-xs text-primary-700">
            Every forecast month's net requirement is zero — available stock (WMS + consignment) already
            covers the forecast, so there is nothing to schedule. This is not a capacity-rule problem:
            your capacity rules only take effect once a product's forecast exceeds its available stock.
          </p>
        </div>
      )}

      {/* Named callout for skipped intent products (design D9) — without
          this a planner sees a run that's simply short some products, with
          no way to tell "not scheduled because no BOM/capacity fits" (a
          real gap) from "not scheduled because it isn't a real material
          yet" (expected, not a gap at all). */}
      {run && skippedIntent.length > 0 && (
        <div role="status" className="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
          <p className="flex items-center gap-1.5 font-medium">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            {skippedIntent.length} intent product{skippedIntent.length === 1 ? '' : 's'} not scheduled this run:
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {skippedIntent.map((item) => (
              // item.qty is already a number here (summed across this
              // product's months by the aggregation above) — not the raw
              // Decimal-as-string the wire sends per line.
              <li key={item.code}>Not scheduled (intent): {item.name} · {formatQty(item.qty)} kg</li>
            ))}
          </ul>
        </div>
      )}

      {exceptionsQuery.isError && (
        <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {errMsg(exceptionsQuery.error, 'Could not load capacity exceptions — maintenance-week markers below may be incomplete.')}
        </p>
      )}

      {run && run.lines.length > 0 && (
        <ProductionMatrix
          key={run.id}
          lines={run.lines}
          weekGrid={run.week_grid}
          maintenanceWeekStarts={maintenanceWeekStarts}
          maintenanceDataUnready={exceptionsQuery.isLoading || exceptionsQuery.isError}
          onOpenWeekDrawer={setWeekDrawerTarget}
          materialsByCode={materialsByCode}
          noBomCodes={noBomCodes}
          unitScale={displayUnit === 't' ? 1000 : 1}
          formatValue={formatValue}
          readOnly={isReleased || !canExecute}
          onAdjustCell={handleAdjustCell}
        />
      )}

      {pickerLines && (
        <AdjustCellPicker
          lines={pickerLines}
          materialsByCode={materialsByCode}
          formatValue={formatValue}
          onPick={(line) => { setPickerLines(null); setAdjustTarget(line) }}
          onClose={() => setPickerLines(null)}
        />
      )}

      {adjustTarget && runId && run && (
        <AdjustDrawer
          runId={runId}
          line={adjustTarget}
          weekGrid={run.week_grid}
          allLines={run.lines}
          onClose={() => setAdjustTarget(null)}
          onSaved={() => { void invalidateRun() }}
          notifySuccess={toasts.success}
          notifyError={toasts.error}
        />
      )}

      {weekDrawerTarget && (() => {
        const weekException = findExistingException(exceptionsQuery.data ?? [], {
          week_start: weekDrawerTarget.week_start, scope_type: 'factory', scope_ref: null, constraint_type: 'max_output_qty',
        })
        // A `max_sku_count` exception that closes this week is a shutdown
        // WeekDrawer does not own (it manages the max_output_qty row only)
        // and cannot undo — it refuses to write rather than adding a second
        // row that says the same thing. Part of the key below for the same
        // reason `weekException` is: this arriving late must remount the
        // drawer, not merely re-render it past its mount-time useState.
        const weekSkuClosure = findSkuClosure(exceptionsQuery.data ?? [], weekDrawerTarget.week_start)
        return (
        <WeekDrawer
          // Round 2 fix: `maintenance`/`reason` are useState, initialized
          // ONCE at mount from `existingException` — but the exceptions
          // query can still be loading (or errored-then-refetched) at the
          // moment this drawer first mounts, racing the much heavier
          // GET /runs/{id}. Without a key, the prop updating later does
          // NOT re-run those initializers, so an already-marked week can
          // mount unchecked/empty-reason and then have Save PATCH
          // `is_active: false` on a week the planner never actually
          // unmarked (same wrong-initial-state class as round 1's finding
          // #3, one tick later). Keying on the resolved row's identity
          // (falling back to a "loading"/"none" sentinel while unresolved)
          // forces React to unmount+remount — not just re-render — once
          // the real exception state lands, so the checkbox/reason always
          // initialize from data that has actually arrived. An effect
          // syncing state on `existingException` changes would also work,
          // but would need its own guard against clobbering an
          // in-progress edit; the key is simpler and has no such edge case
          // (a fresh mount naturally starts from the just-arrived props).
          key={`${weekDrawerTarget.week_start}::${exceptionsQuery.isLoading ? 'loading' : `${weekException?.id ?? 'none'}::${weekSkuClosure?.id ?? 'none'}`}`}
          week={weekDrawerTarget}
          existingException={weekException}
          skuClosure={weekSkuClosure}
          existingExceptionLoading={exceptionsQuery.isLoading}
          existingExceptionError={exceptionsQuery.isError ? errMsg(exceptionsQuery.error, 'Could not load this week\'s current exception state.') : null}
          canWrite={canWriteParams}
          onClose={() => setWeekDrawerTarget(null)}
          onSaved={() => { void invalidateRun(); void invalidateExceptions() }}
          // Round 1 fix #5a: the toolbar's own Recalculate button already
          // guards on isReleased (a released run 409s recalculate) — the
          // toast's action button must match, or the planner gets an error
          // toast for clicking exactly what this drawer told them to click.
          onRecalculate={isReleased ? undefined : () => { void handleRecalculate() }}
          notifySuccess={toasts.success}
          notifyError={toasts.error}
        />
        )
      })()}

      {releaseConfirmOpen && run && releaseSummary && (
        <ConfirmDialog
          title={`Confirm & Release ${run.run_no}`}
          confirmLabel="Confirm & Release"
          onConfirm={handleConfirmRelease}
          onCancel={() => setReleaseConfirmOpen(false)}
          busy={releasing}
          danger={releaseSummary.gapCount > 0 || !isLatestOutlook}
        >
          {/* Task 10 (design §8, "one active released plan"): confirm-release
              always overwrites the entire active mrp_demands MPS lineage,
              regardless of which confirmed outlook produced it (T4 dropped
              the single-confirmed-version invariant, so several outlooks
              can be confirmed at once). Releasing a non-latest outlook is
              legitimate (deliberate rollback) so this warns, it does not
              block — the button below stays enabled either way. */}
          {!isLatestOutlook && (
            <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-danger-800">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                This MPS was generated from an older outlook
                {runVersion ? <> (<strong>{runVersion.version_no}</strong> / anchor <strong>{runVersion.source_anchor_month ?? '—'}</strong>)</> : ''},
                not the latest. Releasing it will overwrite the active MRP plan with that older forecast.
              </span>
            </p>
          )}
          <p>
            This will release <strong>{releaseSummary.productCount}</strong> product{releaseSummary.productCount === 1 ? '' : 's'} totaling{' '}
            <strong>{formatQty(releaseSummary.totalQty)} kg</strong> into the MRP requirements table, and{' '}
            <strong>{releaseSummary.prebuildCount}</strong> line{releaseSummary.prebuildCount === 1 ? '' : 's'} {releaseSummary.prebuildCount === 1 ? 'is' : 'are'} pre-built early.
          </p>
          <p>This run becomes immutable once released — no further locking, adjusting, or recalculating.</p>
          {releaseSummary.gapCount > 0 && (
            <p className="flex items-start gap-1.5 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-warning-800">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                <strong>{releaseSummary.gapCount}</strong> line{releaseSummary.gapCount === 1 ? '' : 's'} {releaseSummary.gapCount === 1 ? 'has' : 'have'} an unresolved capacity gap and will be
                left out of this release entirely — that demand will not be planned this cycle.
              </span>
            </p>
          )}
        </ConfirmDialog>
      )}

      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  )
}
