// Production Plan / MPS — design §6.6 page 3, Phase 1B Task 6. The most
// valuable screen of the feature: generate a Master Production Schedule off
// a confirmed forecast version, see per-month capacity occupancy, review/
// adjust individual plan lines, and Confirm & Release into mrp_demands
// (Phase 1C's material requirements calc consumes that table next).
//
// Backend is mrp-api's /mps/* (app/api/v1/mps.py, Task 4) — see mpsApi.ts's
// header for the endpoint contracts and permission keys. Not wired into
// nav/routes here — that's Task 7.
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Sparkles, AlertTriangle } from 'lucide-react'
import { Button, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { ToastStack } from '@/components/Toast'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { StatusBadge } from '@/components/StatusBadge'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { materialsApi, type MaterialOption } from '@/lib/materials'
import { forecastApi } from '@/pages/forecast/forecastApi'
import { mpsApi, type MpsLine } from './mpsApi'
import { CapacityBars } from './CapacityBars'
import { MpsLineTable } from './MpsLineTable'
import { AdjustDrawer } from './AdjustDrawer'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

export default function ProductionPlanPage() {
  const queryClient = useQueryClient()
  const toasts = useToasts()
  const permsQuery = usePermissions()
  const canExecute = !!permsQuery.data?.permissions['mrp.run.execute']
  const canRelease = !!permsQuery.data?.permissions['mrp.proposal.confirm']

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

  // ── The active run ───────────────────────────────────────────────────────
  const [runId, setRunId] = useState<string | null>(null)

  const runQuery = useQuery({
    queryKey: ['mps-run', runId],
    queryFn: () => mpsApi.get(runId as string),
    enabled: !!runId,
  })
  const run = runQuery.data ?? null
  const isReleased = run?.status === 'released'

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

  // ── Generate / Recalculate ──────────────────────────────────────────────
  const [generating, setGenerating] = useState(false)
  const [recalculating, setRecalculating] = useState(false)

  async function handleGenerate() {
    if (!selectedVersionId) return
    setGenerating(true)
    try {
      const result = await mpsApi.generate(selectedVersionId)
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

  // ── Lock / Unlock selected ──────────────────────────────────────────────
  const [bulkBusy, setBulkBusy] = useState(false)

  async function handleSetLocked(lineIds: string[], locked: boolean) {
    if (!runId || lineIds.length === 0) return
    setBulkBusy(true)
    try {
      await Promise.all(lineIds.map((id) => mpsApi.adjustLine(runId, id, { locked_by_planner: locked })))
      toasts.success(`${locked ? 'Locked' : 'Unlocked'} ${lineIds.length} line${lineIds.length === 1 ? '' : 's'}.`)
      await invalidateRun()
    } catch (err) {
      toasts.error(errMsg(err, `Could not ${locked ? 'lock' : 'unlock'} the selected line(s) — please retry.`))
    } finally {
      setBulkBusy(false)
    }
  }

  // ── Adjust one line ──────────────────────────────────────────────────────
  const [adjustTarget, setAdjustTarget] = useState<MpsLine | null>(null)

  // ── Confirm & Release ────────────────────────────────────────────────────
  const [releaseConfirmOpen, setReleaseConfirmOpen] = useState(false)
  const [releasing, setReleasing] = useState(false)

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

  const actionBusy = bulkBusy || recalculating

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-neutral-900">Production Plan</h1>
        {run && (
          <div className="flex items-center gap-2 text-sm text-neutral-600">
            <span className="font-mono text-xs text-neutral-500">{run.run_no}</span>
            <StatusBadge status={run.status} />
          </div>
        )}
      </div>

      {/* ── Toolbar: version picker + Generate/Recalculate ─────────────── */}
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
        </div>

        {canExecute && (
          <div className="flex items-center gap-2">
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
          </div>
        )}
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

      {run && run.lines.length > 0 && (
        <>
          <CapacityBars occupancy={run.capacity_occupancy} />

          <MpsLineTable
            key={run.id}
            lines={run.lines}
            materialsByCode={materialsByCode}
            readOnly={isReleased}
            canExecute={canExecute}
            canRelease={canRelease}
            actionBusy={actionBusy}
            onLockSelected={(ids) => handleSetLocked(ids, true)}
            onUnlockSelected={(ids) => handleSetLocked(ids, false)}
            onAdjustLine={setAdjustTarget}
            onConfirmReleaseClick={() => setReleaseConfirmOpen(true)}
          />
        </>
      )}

      {adjustTarget && runId && (
        <AdjustDrawer
          runId={runId}
          line={adjustTarget}
          onClose={() => setAdjustTarget(null)}
          onSaved={() => { void invalidateRun() }}
          notifySuccess={toasts.success}
          notifyError={toasts.error}
        />
      )}

      {releaseConfirmOpen && run && releaseSummary && (
        <ConfirmDialog
          title={`Confirm & Release ${run.run_no}`}
          confirmLabel="Confirm & Release"
          onConfirm={handleConfirmRelease}
          onCancel={() => setReleaseConfirmOpen(false)}
          busy={releasing}
          danger={releaseSummary.gapCount > 0}
        >
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
