// Inventory → Trace (design §7.4, D13): one material — or one batch group of
// it — NC (WMS warehouses only) vs WMS movements side by side, from WMS
// go-live to the chosen date, with running balances, pairing and the first
// divergence.
//
// Two routes share this file:
//   /trace            — the entry form (material, optional batch, date)
//   /trace/:material  — one tab per material; batch and date in the query.
// Dates are plain "YYYY-MM-DD" strings on Toronto's clock, never parsed.
import { WmsHistoryNote } from '@/components/WmsHistoryNote'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Download, Loader2, Route } from 'lucide-react'
import { Badge, Skeleton } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatQty, isZero } from '@/lib/number'
import { todayToronto } from '@/lib/time'
import { ErrorBox } from '@/pages/nc/NcUi'
import { rowHidden, traceApi, type TraceLine, type TraceMaterialOption, type TraceResponse } from './traceApi'
import { normCode, useOpenTrace } from './traceNav'
import { MovementsTable, SummaryTable } from './TraceRows'
import { BatchPicker, MaterialPicker } from './TracePickers'
import { exportTrace } from './exportTrace'

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

const inputCls =
  'h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400'
const btnCls =
  'inline-flex h-9 items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-50'

export default function TracePage() {
  const { material } = useParams()
  if (!material) return <TraceStart />
  return <TraceView material={normCode(material)} />
}

// ── /trace: entry form ─────────────────────────────────────────────────────

function TraceStart() {
  const today = todayToronto()
  const openTrace = useOpenTrace()
  const [material, setMaterial] = useState<TraceMaterialOption | null>(null)
  const [batch, setBatch] = useState('')
  const [date, setDate] = useState(today)
  const noBatchSince = material?.no_batch_since ?? null
  const batchLocked = !!noBatchSince && noBatchSince <= date

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!material) return
    openTrace(material.code, { batch: (!batchLocked && batch) || null, date: date && date !== today ? date : null })
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold text-neutral-900">Trace</h1>
        <p className="text-xs text-neutral-500">
          NC and WMS movements of one material side by side, from the start of WMS history (2025-02-03) to a date — with running
          balances, pairing and the first point where the two stop agreeing.
        </p>
      </header>
      <form onSubmit={submit}
        className="flex flex-wrap items-end gap-3 rounded-lg border border-neutral-200 bg-white px-3 py-3">
        <div className="flex flex-col gap-1 text-xs text-neutral-600">
          <label htmlFor="trace-material">Material</label>
          <MaterialPicker id="trace-material" value={material?.code ?? ''} autoFocus className="w-64"
            onChange={(o) => { if (o.code !== material?.code) setBatch(''); setMaterial(o) }}
            onDeselect={() => { setMaterial(null); setBatch('') }} />
        </div>
        <div className="flex flex-col gap-1 text-xs text-neutral-600">
          <label htmlFor="trace-batch">Batch (optional)</label>
          <BatchPicker key={material?.code ?? ''} id="trace-batch" material={material?.code ?? null}
            value={batch} onChange={(b) => setBatch(b)} className="w-72"
            lockedNote={batchLocked ? noBatchNote(noBatchSince!) : null} />
        </div>
        <label className="flex flex-col gap-1 text-xs text-neutral-600">
          To date
          <input type="date" value={date} max={today} onChange={(e) => setDate(e.target.value || today)} className={inputCls} />
        </label>
        <button type="submit" disabled={!material}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-4 text-sm font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50">
          <Route aria-hidden className="h-4 w-4" />Trace
        </button>
        {batchLocked && <p className="basis-full text-xs text-neutral-500">{noBatchNote(noBatchSince!)}</p>}
        <p className="basis-full text-[11px] text-neutral-400">
          Leave the batch empty to trace the whole material. A batch number matches its whole batch group
          (NC batch, WMS QC batch or supplier batch).
        </p>
      </form>
    </div>
  )
}

// ── /trace/:material ───────────────────────────────────────────────────────

function Tile({ label, children, tone }: { label: string; children: React.ReactNode; tone?: 'bad' }) {
  return (
    <div className={cn('min-w-[8rem] rounded-lg border bg-white px-3 py-2',
      tone === 'bad' ? 'border-danger-200' : 'border-neutral-200')}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      <div className="mt-0.5 font-mono text-sm text-neutral-800">{children}</div>
    </div>
  )
}

function noBatchNote(since: string): string {
  return `NC stopped recording this material by batch on ${since} — material-level trace only.`
}

function findRowIndex(t: TraceResponse, match: (ln: TraceLine, kind: string) => boolean): number {
  return t.rows.findIndex((r) => [...r.nc, ...r.wms].some((ln) => match(ln, r.kind)))
}

function TraceView({ material }: { material: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const openTrace = useOpenTrace()
  const today = todayToronto()
  const rawDate = searchParams.get('date')
  const date = rawDate && DATE_ONLY.test(rawDate) && rawDate <= today ? rawDate : today
  const batch = (searchParams.get('batch') ?? '').trim()

  const [showHidden, setShowHidden] = useState(false)
  const [highlight, setHighlight] = useState<{ index: number; n: number } | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<unknown>(null)

  function setParams(changes: Record<string, string>) {
    const next = new URLSearchParams(searchParams)
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    setSearchParams(next, { replace: true })
  }

  const q = useQuery({
    queryKey: ['trace', material, batch, date],
    queryFn: () => traceApi.get(material, batch || null, date),
    placeholderData: keepPreviousData,
    retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
  })
  // keepPreviousData would show another batch's / date's trace under this
  // header while loading; only reuse it when it is the same material.
  const t = q.data && q.data.material.code === material ? q.data : undefined

  // A highlight belongs to one response; a new batch/date clears it.
  const dataKey = `${batch}|${date}`
  const [highlightKey, setHighlightKey] = useState(dataKey)
  if (highlightKey !== dataKey) {
    setHighlightKey(dataKey)
    setHighlight(null)
  }

  const rowRefs = useRef(new Map<number, HTMLTableRowElement>())
  const registerRow = useCallback((i: number, el: HTMLTableRowElement | null) => {
    if (el) rowRefs.current.set(i, el)
    else rowRefs.current.delete(i)
  }, [])
  useEffect(() => {
    if (!highlight) return
    rowRefs.current.get(highlight.index)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [highlight])

  const indices = useMemo(() => {
    if (!t) return []
    return t.rows.map((_, i) => i).filter((i) => showHidden || !rowHidden(t.rows[i]))
  }, [t, showHidden])
  const hiddenRows = t ? t.rows.length - indices.length : 0

  function jumpTo(index: number) {
    if (!t || index < 0) return
    if (rowHidden(t.rows[index])) setShowHidden(true)
    setHighlight((h) => ({ index, n: (h?.n ?? 0) + 1 }))
  }

  const div = t?.divergence ?? null
  const divIndex = t && div ? findRowIndex(t, (ln) => ln.side === div.side && ln.id === div.id) : -1
  const mainIndex = t && div?.main
    ? findRowIndex(t, (ln, kind) => {
        const m = div.main!
        return kind === `${m.side}_only` && ln.side === m.side
          && (m.doc != null ? ln.doc === m.doc : ln.time === m.time && ln.category === m.category)
      })
    : -1

  const batchLocked = !!t?.no_batch_since && t.no_batch_since <= date
  // What the batch box shows: the group label picked from the list, else the
  // one the trace came back with (a link carries only the batch number).
  const [pickedLabels, setPickedLabels] = useState<Record<string, string>>({})
  const batchDisplay = !batch ? ''
    : pickedLabels[batch] ?? (t?.batch === batch && t.batch_group ? t.batch_group : batch)
  const batchRefused = q.isError && !!batch && q.error instanceof ApiError && q.error.status === 422

  async function onExport() {
    if (!t) return
    setExporting(true)
    setExportError(null)
    try {
      await exportTrace(t)
    } catch (e) {
      setExportError(e)
    } finally {
      setExporting(false)
    }
  }

  const scopeEmpty = !!t?.batch
    && (t.batch_scope.nc_batches?.length ?? 0) === 0 && (t.batch_scope.wms_lots?.length ?? 0) === 0

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="flex flex-wrap items-baseline gap-x-2 text-xl font-semibold text-neutral-900">
            Trace <span className="font-mono">{material}</span>
            {t?.material.name && <span className="text-base font-normal text-neutral-600">{t.material.name}</span>}
            {t?.material.uom && <span className="text-xs font-normal text-neutral-400">{t.material.uom}</span>}
            {t && !t.material.in_nc && <Badge variant="warning" className="text-[10px]">Not in NC</Badge>}
          </h1>
          {t && (
            <p className="text-xs text-neutral-500">
              From {t.start} (WMS history start) to {t.date}
              <span className="text-neutral-400"> · {t.scope_note} NC clock: {t.nc_time_zone}.</span>
            </p>
          )}
          {t && <WmsHistoryNote info={t.wms_history} />}
        </div>
        <div className="flex items-center gap-2">
          <Link to="/trace" className="text-xs font-medium text-primary-700 hover:underline">Trace another material</Link>
          <button type="button" onClick={onExport} disabled={exporting || !t} className={btnCls}>
            {exporting ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Download aria-hidden className="h-4 w-4" />}
            {exporting ? 'Exporting…' : 'Export'}
          </button>
        </div>
      </header>

      <div className="flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-neutral-600">
            <label htmlFor="trace-view-material">Material</label>
            <MaterialPicker key={material} id="trace-view-material" value={material} className="w-56"
              onChange={(o) => {
                if (o.code !== material) openTrace(o.code, { date: date !== today ? date : null })
              }} />
          </div>
          <div className="flex items-center gap-2 text-xs text-neutral-600">
            <label htmlFor="trace-view-batch">Batch</label>
            <BatchPicker key={`${material}|${batch}|${batchDisplay}`} id="trace-view-batch" material={material}
              value={batch} display={batchDisplay}
              lockedNote={batchLocked ? noBatchNote(t!.no_batch_since!) : null}
              onChange={(b, label) => {
                if (b) setPickedLabels((m) => ({ ...m, [b]: label }))
                setParams({ batch: b })
              }} className="w-72" />
          </div>
          <label className="flex items-center gap-2 text-xs text-neutral-600">
            To date
            <input type="date" value={date} max={today}
              onChange={(e) => setParams({ date: e.target.value && e.target.value !== today ? e.target.value : '' })}
              className={inputCls} />
          </label>
          {q.isFetching && <Loader2 aria-label="Loading" className="h-4 w-4 animate-spin text-neutral-400" />}
        </div>
        {batchLocked && <p className="text-xs text-neutral-500">{noBatchNote(t!.no_batch_since!)}</p>}
        {t?.batch && (
          <div className="flex flex-col gap-1 text-xs text-neutral-600">
            <span>
              Batch group <span className="font-mono font-medium text-neutral-800">{t.batch_group ?? t.batch}</span>
              {t.batch_group && t.batch_group !== t.batch && <span className="text-neutral-400"> (from “{t.batch}”)</span>}
            </span>
            {scopeEmpty ? (
              <p className="flex items-center gap-1.5 text-warning-800">
                <AlertTriangle aria-hidden className="h-3.5 w-3.5" />
                No NC batch or WMS lot of {material} matches “{t.batch}” — check the number, or clear the batch.
              </p>
            ) : (
              <details className="text-[11px] text-neutral-500">
                <summary className="cursor-pointer select-none">
                  {t.batch_scope.nc_batches?.length ?? 0} NC batch(es) · {t.batch_scope.wms_lots?.length ?? 0} WMS lot(s) in this group
                </summary>
                <div className="mt-1 grid gap-1 sm:grid-cols-2">
                  <p><span className="text-neutral-400">NC batches: </span><span className="font-mono">{t.batch_scope.nc_batches?.join(', ') || '—'}</span></p>
                  <p><span className="text-neutral-400">WMS lots: </span><span className="font-mono">{t.batch_scope.wms_lots?.join(', ') || '—'}</span></p>
                </div>
              </details>
            )}
          </div>
        )}
      </div>

      {q.isError && <ErrorBox error={q.error} fallback="Could not load the trace." />}
      {batchRefused && (
        <button type="button" onClick={() => setParams({ batch: '' })} className={cn(btnCls, 'w-fit')}>
          Trace the whole material instead
        </button>
      )}
      {exportError != null && <ErrorBox error={exportError} fallback="Export failed." />}

      {q.isLoading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      )}

      {t && (
        <>
          {/* Conclusion */}
          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              <Tile label="Opening">
                <span className="text-neutral-400">NC </span>{formatQty(t.opening.nc)}
                <span className="text-neutral-400"> · WMS </span>{formatQty(t.opening.wms)}
              </Tile>
              <Tile label={`Closing ${t.date}`} tone={isZero(t.closing.diff) ? undefined : 'bad'}>
                <span className="text-neutral-400">NC </span>{formatQty(t.closing.nc)}
                <span className="text-neutral-400"> · WMS </span>{formatQty(t.closing.wms)}
                <span className="text-neutral-400"> · diff </span>
                <span className={isZero(t.closing.diff) ? 'text-neutral-500' : 'font-semibold text-danger-600'}>
                  {formatQty(t.closing.diff)}
                </span>
              </Tile>
              <Tile label="Movements">
                <span className="text-xs">
                  <span className="text-neutral-400">paired by document </span>{t.counts.paired_by_document}
                  <span className="text-neutral-400"> · by quantity </span>{t.counts.paired_by_quantity}
                  <span className="text-neutral-400"> · NC only </span>
                  <span className={t.counts.nc_only ? 'text-danger-600' : undefined}>{t.counts.nc_only}</span>
                  <span className="text-neutral-400"> · WMS only </span>
                  <span className={t.counts.wms_only ? 'text-danger-600' : undefined}>{t.counts.wms_only}</span>
                </span>
              </Tile>
            </div>

            {div ? (
              <div role="alert" className="flex flex-col gap-1.5 rounded-lg border-2 border-danger-300 bg-danger-50 px-4 py-3">
                <p className="flex items-center gap-1.5 text-sm font-semibold text-danger-800">
                  <AlertTriangle aria-hidden className="h-4 w-4" />First divergence
                  {div.diverged_from_start && (
                    <span className="text-xs font-normal text-danger-700">— the two balances never agree in this window</span>
                  )}
                </p>
                <button type="button" onClick={() => jumpTo(divIndex)} disabled={divIndex < 0}
                  className="w-fit text-left text-sm text-danger-900 underline decoration-danger-300 underline-offset-2 hover:decoration-danger-700 disabled:no-underline"
                  title={divIndex >= 0 ? 'Show this movement in the table below' : undefined}>
                  {div.summary}
                </button>
                {div.main && (
                  <p className="text-xs text-danger-800">
                    Largest unmatched since then:{' '}
                    <button type="button" onClick={() => jumpTo(mainIndex)} disabled={mainIndex < 0}
                      className="text-left underline decoration-danger-300 underline-offset-2 hover:decoration-danger-700 disabled:no-underline"
                      title={mainIndex >= 0 ? 'Show this document in the table below' : undefined}>
                      {div.main.summary}
                    </button>
                  </p>
                )}
              </div>
            ) : isZero(t.closing.diff) ? (
              <p className="flex items-center gap-1.5 rounded-lg border border-success-200 bg-success-50 px-4 py-3 text-sm text-success-800">
                <CheckCircle2 aria-hidden className="h-4 w-4" />NC and WMS agree at the end of the window.
              </p>
            ) : (
              <p className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600">
                The closing difference is within the quantity tolerance — no divergence to point at.
              </p>
            )}
          </section>

          {/* Category summary + movements */}
          <section className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-neutral-800">By category</h2>
              <label className="flex items-center gap-2 text-xs text-neutral-600">
                <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)}
                  className="h-4 w-4 rounded border-neutral-300" />
                Show internal moves &amp; state changes
              </label>
            </div>
            <SummaryTable t={t} showHidden={showHidden} />
          </section>

          <section className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold text-neutral-800">Movements</h2>
              <span className="text-[11px] text-neutral-400">
                {indices.length.toLocaleString('en-US')} row(s) shown
                {hiddenRows > 0 && ` · ${hiddenRows.toLocaleString('en-US')} internal / state-change row(s) hidden`}
                {' · '}balances are each side's running total; NC opening {formatQty(t.opening.nc)}, WMS starts at 0
              </span>
            </div>
            <MovementsTable key={dataKey} rows={t.rows} indices={indices} highlight={highlight?.index ?? null} registerRow={registerRow}
              closing={t.closing} date={t.date} />
          </section>
        </>
      )}
    </div>
  )
}
