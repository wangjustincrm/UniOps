// Read-only viewer for a frozen forecast outlook snapshot (Continuous Sales
// Forecast follow-up #2, "Outlook list + viewer") — opened from
// SalesForecastPage's Outlooks panel below the live grid. Fetches the
// immutable snapshot via forecastApi.getGrid(versionId) (the
// GET /forecast/versions/{id}/grid endpoint kept for this purpose — see
// forecastApi.ts's header) and renders it through <MatrixGrid readOnly>, the
// same component the live grid uses. Respects the page's KG/Tonne
// displayUnit toggle (formatValue/unitScale passed in by the caller) so the
// numbers shown here match what the planner would see live — the underlying
// snapshot is always stored in kg, same as the live series.
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Sparkles } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import { MatrixGrid, type GridRow as MatrixRow, type GridCol as MatrixCol } from '@/components/MatrixGrid'
import { cellKey } from '@/components/matrixGrid/pasteLogic'
import type { CellValueMap } from '@/components/matrixGrid/history'
import { forecastApi, type ForecastVersion, type GridResponse } from './forecastApi'

import { Modal } from '@uniops/shell'
function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

/** Same sparse-map construction as SalesForecastPage's buildBaseline — kept
 *  as an independent copy rather than importing it from that page (a page
 *  component isn't a module other components should depend on); both build
 *  off the identical GridResponse shape shared by forecastApi.ts/seriesApi.ts. */
function buildCellMap(grid: GridResponse | undefined): CellValueMap {
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

function noop() {
  // MatrixGrid requires onChange even when readOnly — a read-only grid
  // never calls it after the initial mount (see MatrixGrid's emit-on-mount
  // guard: lastEmitted starts equal to the initial cells, so the effect
  // that calls onChange doesn't fire on first render).
}

export function OutlookViewerModal({
  version, onClose, formatValue, unitScale, rowTotalLabel, colTotalLabel,
}: {
  version: ForecastVersion
  onClose: () => void
  formatValue: (n: number) => string
  unitScale: number
  rowTotalLabel?: string
  colTotalLabel?: string
}) {
  const gridQuery = useQuery({
    queryKey: ['forecast-version-grid', version.id],
    queryFn: () => forecastApi.getGrid(version.id),
    staleTime: 5 * 60_000, // an immutable, confirmed snapshot — it never changes once frozen
  })

  const cellMap = useMemo(() => buildCellMap(gridQuery.data), [gridQuery.data])
  const matrixCols: MatrixCol[] = useMemo(
    () => (gridQuery.data?.months ?? []).map((m) => ({ id: m, label: m })),
    [gridQuery.data],
  )
  const matrixRows: MatrixRow[] = useMemo(
    () => (gridQuery.data?.rows ?? []).map((r) => ({ id: r.material_code, label: r.name ?? r.material_code })),
    [gridQuery.data],
  )

  return (
    <Modal
      open
      onClose={onClose}
      title={`Outlook ${version.version_no}`}
      icon={<Sparkles className="h-4 w-4" />}
      description={<>Anchor {version.source_anchor_month ?? '—'} · {version.horizon_months}-month horizon{version.confirmed_at && <> · frozen {formatDateTime(version.confirmed_at)}</>}{' '}— read-only snapshot.</>}
      closeOnEscape={false}
      size="full"
      className="max-w-6xl"
      bodyClassName="flex min-h-0 flex-col p-0"
    >
        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {gridQuery.isLoading ? (
            <p role="status" className="flex items-center justify-center gap-1.5 py-12 text-sm text-fg-muted">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading snapshot…
            </p>
          ) : gridQuery.isError ? (
            <p role="alert" className="rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
              {errMsg(gridQuery.error, 'Could not load this outlook.')}
            </p>
          ) : matrixRows.length === 0 ? (
            <p className="py-12 text-center text-sm text-fg-muted">This snapshot has no product rows.</p>
          ) : (
            <MatrixGrid
              rows={matrixRows}
              cols={matrixCols}
              value={cellMap}
              onChange={noop}
              readOnly
              height={480}
              rowHeaderLabel="Product"
              rowTotalLabel={rowTotalLabel ?? 'Total'}
              colTotalLabel={colTotalLabel ?? 'Monthly Sum'}
              formatValue={formatValue}
              unitScale={unitScale}
            />
          )}
        </div>
    </Modal>
  )
}
