// Sales Forecast — design spec §6.6 page 1. Rows = products, columns = the
// selected version's 18 months, editable via MatrixGrid (Excel paste,
// undo/redo — see Task 9). Version switcher top-right; Save Draft bulk-
// upserts only the cells the planner actually touched; Confirm locks the
// version (read-only thereafter); Import Excel previews a validation
// report (dry_run) before writing anything; Export / Download Template are
// plain downloads.
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FileDown, FileSpreadsheet, FileUp, Lock, Loader2, Save, ShieldCheck } from 'lucide-react'
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { MatrixGrid, type GridRow as MatrixRow, type GridCol as MatrixCol } from '@/components/MatrixGrid'
import { cellKey, parseCellKey } from '@/components/matrixGrid/pasteLogic'
import type { CellValueMap } from '@/components/matrixGrid/history'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { VersionSwitcher } from './VersionSwitcher'
import { NewVersionModal } from './NewVersionModal'
import { ImportWizard } from './ImportWizard'
import { forecastApi, saveBlob, type CreateVersionBody } from './forecastApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function buildBaseline(grid: { months: string[]; rows: { material_code: string; cells: Record<string, string> }[] } | undefined): CellValueMap {
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

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
}

export default function ForecastPage() {
  const queryClient = useQueryClient()
  const toasts = useToasts()

  // null = "no explicit choice yet" -> defaults to the newest version once
  // the list loads. Derived at render time (not via a setState-in-effect)
  // so there's no extra render pass on load.
  const [manualVersionId, setManualVersionId] = useState<string | null>(null)
  const [liveCells, setLiveCells] = useState<CellValueMap>(new Map())
  const [syncedBaseline, setSyncedBaseline] = useState<CellValueMap>(new Map())
  const [newVersionOpen, setNewVersionOpen] = useState(false)
  const [newVersionBusy, setNewVersionBusy] = useState(false)
  const [newVersionError, setNewVersionError] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [downloadingTemplate, setDownloadingTemplate] = useState(false)

  const versionsQuery = useQuery({
    queryKey: ['forecast-versions'],
    queryFn: () => forecastApi.listVersions(1, 100),
  })

  const versions = useMemo(() => versionsQuery.data?.items ?? [], [versionsQuery.data])

  // Default to the most recently created version (list is created_at desc)
  // until the planner explicitly picks one.
  const selectedVersionId = manualVersionId ?? versions[0]?.id ?? null
  const selectedVersion = versions.find((v) => v.id === selectedVersionId) ?? null
  const isDraft = selectedVersion?.status === 'draft'

  const gridQuery = useQuery({
    queryKey: ['forecast-grid', selectedVersionId],
    queryFn: () => forecastApi.getGrid(selectedVersionId as string),
    enabled: !!selectedVersionId,
  })

  const baseline = useMemo(() => buildBaseline(gridQuery.data), [gridQuery.data])

  // liveCells is the single source of truth for dirty-diffing. MatrixGrid's
  // `value` prop only seeds its *internal* history on mount (see the file
  // header comment there) — it does not call onChange for that initial
  // seed — so liveCells needs its own sync whenever a fresh server snapshot
  // lands (load, version switch, or a refetch after save/import/confirm).
  // Done as a render-phase state adjustment (same pattern as MatrixCell in
  // MatrixGrid.tsx: compare against the last-synced reference and update
  // during render) rather than a setState-in-effect, which the stricter
  // react-hooks lint rule flags as a cascading-render risk.
  if (baseline !== syncedBaseline) {
    setSyncedBaseline(baseline)
    setLiveCells(baseline)
  }

  const dirtyCells = useMemo(() => {
    const out: { material_code: string; month: string; qty: number }[] = []
    const keys = new Set<string>([...baseline.keys(), ...liveCells.keys()])
    for (const key of keys) {
      const b = baseline.get(key) ?? 0
      const c = liveCells.get(key) ?? 0
      if (b !== c) {
        const { rowId, colId } = parseCellKey(key)
        out.push({ material_code: rowId, month: colId, qty: c })
      }
    }
    return out
  }, [baseline, liveCells])

  const matrixRows: MatrixRow[] = useMemo(
    () => (gridQuery.data?.rows ?? []).map((r) => ({ id: r.material_code, label: r.name ?? r.material_code })),
    [gridQuery.data],
  )
  const matrixCols: MatrixCol[] = useMemo(
    () => (gridQuery.data?.months ?? []).map((m) => ({ id: m, label: m })),
    [gridQuery.data],
  )

  // Remount MatrixGrid whenever a fresh server snapshot lands (version
  // switch, or a refetch after save/import/confirm) — the component only
  // resets its undo history and "modified" markers on remount (see Task 9
  // report: "remount with a different key to load a different dataset").
  const gridKey = `${selectedVersionId ?? 'none'}::${gridQuery.dataUpdatedAt}`

  function trySelectVersion(id: string) {
    if (dirtyCells.length > 0) {
      const ok = window.confirm(
        `You have ${dirtyCells.length} unsaved cell edit(s) on ${selectedVersion?.version_no}. Switch versions and discard them?`,
      )
      if (!ok) return
    }
    setManualVersionId(id)
  }

  async function handleCreateVersion(body: CreateVersionBody) {
    setNewVersionBusy(true)
    setNewVersionError(null)
    try {
      const created = await forecastApi.createVersion(body)
      await queryClient.invalidateQueries({ queryKey: ['forecast-versions'] })
      setManualVersionId(created.id)
      setNewVersionOpen(false)
      toasts.success(`Version ${created.version_no} created.`)
    } catch (err) {
      setNewVersionError(errMsg(err, 'Could not create the version.'))
    } finally {
      setNewVersionBusy(false)
    }
  }

  async function handleSaveDraft() {
    if (!selectedVersionId || dirtyCells.length === 0) return
    setSaving(true)
    try {
      const res = await forecastApi.upsertCells(selectedVersionId, dirtyCells)
      const parts = [`${res.upserted} cell${res.upserted === 1 ? '' : 's'} saved`]
      if (res.skipped_frozen.length > 0) {
        parts.push(`${res.skipped_frozen.length} cell${res.skipped_frozen.length === 1 ? '' : 's'} skipped (frozen)`)
      }
      toasts.success(parts.join(', ') + '.')
      await queryClient.invalidateQueries({ queryKey: ['forecast-grid', selectedVersionId] })
    } catch (err) {
      toasts.error(errMsg(err, 'Save failed — please retry.'))
    } finally {
      setSaving(false)
    }
  }

  async function handleConfirmVersion() {
    if (!selectedVersionId) return
    setConfirming(true)
    try {
      const updated = await forecastApi.confirmVersion(selectedVersionId)
      toasts.success(`${updated.version_no} confirmed — it is now read-only.`)
      setConfirmOpen(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['forecast-versions'] }),
        queryClient.invalidateQueries({ queryKey: ['forecast-grid', selectedVersionId] }),
      ])
    } catch (err) {
      toasts.error(errMsg(err, 'Confirm failed — please retry.'))
    } finally {
      setConfirming(false)
    }
  }

  async function handleExport() {
    if (!selectedVersionId || !selectedVersion) return
    setExporting(true)
    try {
      const { blob, filename } = await forecastApi.exportGrid(selectedVersionId)
      saveBlob(blob, filename, `forecast-export-${selectedVersion.version_no}.xlsx`)
      toasts.success('Export downloaded.')
    } catch (err) {
      toasts.error(errMsg(err, 'Export failed — please retry.'))
    } finally {
      setExporting(false)
    }
  }

  async function handleDownloadTemplate() {
    if (!selectedVersionId || !selectedVersion) return
    setDownloadingTemplate(true)
    try {
      const { blob, filename } = await forecastApi.downloadTemplate(selectedVersionId)
      saveBlob(blob, filename, `forecast-template-${selectedVersion.version_no}.xlsx`)
      toasts.success('Template downloaded.')
    } catch (err) {
      toasts.error(errMsg(err, 'Template download failed — please retry.'))
    } finally {
      setDownloadingTemplate(false)
    }
  }

  const grandTotal = useMemo(() => {
    let sum = 0
    for (const v of liveCells.values()) sum += v
    return sum
  }, [liveCells])

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-lg font-semibold text-neutral-900">Sales Forecast</h1>
        <VersionSwitcher
          versions={versions}
          selectedId={selectedVersionId}
          onSelect={trySelectVersion}
          onCreateNew={() => { setNewVersionError(null); setNewVersionOpen(true) }}
        />
      </div>

      {versionsQuery.isError && (
        <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {errMsg(versionsQuery.error, 'Could not load forecast versions.')}
        </p>
      )}

      {!versionsQuery.isLoading && versions.length === 0 && (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-neutral-300 py-16 text-center">
          <p className="text-sm text-neutral-500">No forecast versions yet.</p>
          <Button type="button" size="sm" onClick={() => setNewVersionOpen(true)}>Create the first version</Button>
        </div>
      )}

      {selectedVersionId && (
        <>
          {/* Toolbar */}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={() => setImportOpen(true)} disabled={!isDraft}>
                <FileUp className="h-3.5 w-3.5" /> Import Excel
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleExport} disabled={exporting}>
                {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDown className="h-3.5 w-3.5" />}
                Export
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleDownloadTemplate} disabled={downloadingTemplate}>
                {downloadingTemplate ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileSpreadsheet className="h-3.5 w-3.5" />}
                Download Template
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={handleSaveDraft} disabled={!isDraft || saving || dirtyCells.length === 0}>
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Save Draft{dirtyCells.length > 0 ? ` (${dirtyCells.length})` : ''}
              </Button>
              <Button type="button" size="sm" onClick={() => setConfirmOpen(true)} disabled={!isDraft || confirming}>
                <ShieldCheck className="h-3.5 w-3.5" /> Confirm
              </Button>
            </div>
          </div>

          {!isDraft && selectedVersion && (
            <p className="flex items-center gap-1.5 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              <Lock className="h-3.5 w-3.5" />
              {selectedVersion.status === 'confirmed'
                ? 'This version is confirmed and read-only. Create a new version (optionally copying from this one) to keep editing.'
                : 'This version was superseded by a later confirmed version and is read-only.'}
            </p>
          )}

          {gridQuery.isError && (
            <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {errMsg(gridQuery.error, 'Could not load the grid.')}
            </p>
          )}

          {gridQuery.isLoading ? (
            <p role="status" className="py-12 text-center text-sm text-neutral-400">Loading grid…</p>
          ) : gridQuery.data && (
            <>
              {matrixRows.length === 0 && (
                <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
                  This version has no forecast lines yet. Download the template, fill it in, and use Import Excel — or create
                  a new version copying lines from an existing one.
                </p>
              )}
              <MatrixGrid
                key={gridKey}
                rows={matrixRows}
                cols={matrixCols}
                value={liveCells}
                onChange={setLiveCells}
                readOnly={!isDraft}
                height={560}
                rowHeaderLabel="Product"
                rowTotalLabel="Total"
                colTotalLabel="Monthly Sum"
                formatValue={formatQty}
              />
            </>
          )}
        </>
      )}

      {newVersionOpen && (
        <NewVersionModal
          versions={versions}
          busy={newVersionBusy}
          error={newVersionError}
          onClose={() => setNewVersionOpen(false)}
          onCreate={handleCreateVersion}
        />
      )}

      {importOpen && selectedVersionId && (
        <ImportWizard
          versionId={selectedVersionId}
          onClose={() => setImportOpen(false)}
          onImported={() => queryClient.invalidateQueries({ queryKey: ['forecast-grid', selectedVersionId] })}
          notifySuccess={toasts.success}
        />
      )}

      {confirmOpen && selectedVersion && gridQuery.data && (
        <ConfirmDialog
          title={`Confirm ${selectedVersion.version_no}?`}
          confirmLabel="Confirm & Lock"
          busy={confirming}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={handleConfirmVersion}
        >
          <p>
            This locks <strong>{matrixRows.length}</strong> product{matrixRows.length === 1 ? '' : 's'} across{' '}
            <strong>{matrixCols.length}</strong> months (grand total <strong>{formatQty(grandTotal)}</strong>) as the active
            forecast. It becomes read-only immediately — further changes require a new version.
          </p>
          {dirtyCells.length > 0 && (
            <p className="text-warning-700">
              You have {dirtyCells.length} unsaved edit(s). Save Draft first, or they will be lost.
            </p>
          )}
          <p className="text-neutral-500">Any other currently confirmed version will be marked superseded.</p>
        </ConfirmDialog>
      )}

      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  )
}
