// Reconciliation table pieces: one material row, its expansion (causes, GL by
// source, batch groups), and the small GL tables under the main table.
import { ChevronDown, ChevronRight, Route } from 'lucide-react'
import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import { absExceeds, addDecimal, formatMoney, formatQty, isNegative, isZero } from '@/lib/number'
import { MaterialCell, Uom, thCls } from '@/pages/nc/NcUi'
import { TraceLink } from '@/pages/trace/TraceLink'
import { SIDE_LABELS, type ReconBatchGroup, type ReconCause, type ReconGlExtra, type ReconRow, type ReconStatus } from './reconApi'

const numCls = 'px-3 py-2 text-right font-mono text-xs tabular-nums whitespace-nowrap'
const subNumCls = 'px-3 py-1.5 text-right font-mono tabular-nums whitespace-nowrap'

// Short badge text per cause; the tooltip carries the API's full label.
const SHORT_LABELS: Record<string, string> = {
  nc_stopped: 'NC stopped',
  wms_only_material: 'WMS-only / no NC batches',
  not_costed_warehouse: 'Not-costed warehouse',
  shipped_not_invoiced: 'Shipped, not invoiced',
  unsigned_documents: 'Unsigned documents',
  back_dated: 'Back-dated',
  prior_years: 'Prior years',
  gl_non_ia: 'GL non-IA entries',
  batch_label: 'Batch labels differ',
}

const shortLabel = (code: string, label: string) => SHORT_LABELS[code] ?? label

export function StatusBadge({ status }: { status: ReconStatus }) {
  if (status === 'ok') return <Badge variant="success">OK</Badge>
  if (status === 'explained') return <Badge variant="info">Explained</Badge>
  return <Badge variant="danger">Unexplained</Badge>
}

function CauseBadge({ c, showAmt }: { c: ReconCause; showAmt: boolean }) {
  const title = `${c.label} (${SIDE_LABELS[c.side]}) — qty ${formatQty(c.qty)}`
    + (showAmt && c.amount != null ? `, amount ${formatMoney(c.amount)}` : '')
  return (
    <span title={title} className={cn(
      'inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium',
      c.code === 'batch_label' ? 'bg-purple-50 text-purple-700' : 'bg-neutral-100 text-neutral-600',
    )}>
      {shortLabel(c.code, c.label)}
    </span>
  )
}

/** A difference cell: the diff, red with "unexplained x" underneath when the
 *  residual exceeds tolerance, grey when the diff is zero. */
function DiffCell({ diff, unexplained, tol, money = false }: {
  diff: string | null
  unexplained: string | null
  tol: string
  money?: boolean
}) {
  const fmt = money ? formatMoney : formatQty
  const bad = absExceeds(unexplained, tol)
  return (
    <td className={cn(numCls, diff != null && isZero(diff) && 'text-neutral-300', bad && 'text-danger-600 font-medium')}>
      {fmt(diff)}
      {bad && <div className="text-[10px] font-normal">unexplained {fmt(unexplained)}</div>}
    </td>
  )
}

function Num({ v, money = false, uom }: { v: string | null; money?: boolean; uom?: string | null }) {
  return (
    <td className={cn(numCls, isNegative(v) && 'text-danger-600', v != null && isZero(v) && 'text-neutral-400')}>
      {money ? formatMoney(v) : formatQty(v)}{uom !== undefined && v != null && <Uom uom={uom} />}
    </td>
  )
}

function MatchBadge({ match }: { match: ReconBatchGroup['match'] }) {
  if (match === 'both') return <Badge variant="success" className="text-[10px]">Both</Badge>
  if (match === 'wms_only') return <Badge variant="warning" className="text-[10px]">WMS only</Badge>
  return <Badge variant="warning" className="text-[10px]">NC only</Badge>
}

/** The batch number to trace a batch group by: its first NC batch, else the
 *  first value of its label (the API joins several with " / "). Null for the
 *  "(no batch)" group, which no single number selects. */
function groupTraceBatch(b: ReconBatchGroup): string | null {
  if (b.nc_batches.length > 0) return b.nc_batches[0]
  const first = b.label.split(' / ')[0]?.trim()
  return first && first !== '(no batch)' ? first : null
}

function BatchTraceLink({ material, b, date }: { material: string; b: ReconBatchGroup; date: string }) {
  const batch = groupTraceBatch(b)
  if (!batch) return null
  return (
    <TraceLink material={material} batch={batch} date={date} title={`Trace batch group ${b.label}`}
      className="inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:underline">
      <Route aria-hidden className="h-3.5 w-3.5" />Trace
    </TraceLink>
  )
}

function Expansion({ r, showAmt, tolQty, date, onShowBatches }: {
  r: ReconRow
  showAmt: boolean
  tolQty: string
  date: string
  onShowBatches: () => void
}) {
  const sources = r.gl_by_source ? Object.entries(r.gl_by_source) : []
  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-1.5">
        <h3 className="text-xs font-semibold text-neutral-700">Causes</h3>
        {r.categories.length === 0 ? <p className="text-xs text-neutral-400">None — no cause rule applied to this material.</p> : (
          <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className={thCls}>
                <tr>
                  <th className="px-3 py-1.5 text-left">Cause</th>
                  <th className="px-3 py-1.5 text-left">Side</th>
                  <th className="px-3 py-1.5 text-right">Qty</th>
                  {showAmt && <th className="px-3 py-1.5 text-right">Amount</th>}
                </tr>
              </thead>
              <tbody>
                {r.categories.map((c, i) => (
                  <tr key={`${c.code}-${i}`} className="border-t border-neutral-100">
                    <td className="px-3 py-1.5">{c.label}</td>
                    <td className="px-3 py-1.5 whitespace-nowrap">{SIDE_LABELS[c.side]}</td>
                    <td className={subNumCls}>{formatQty(c.qty)}</td>
                    {showAmt && <td className={subNumCls}>{c.amount != null ? formatMoney(c.amount) : '—'}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {showAmt && r.gl_by_source && (
        <section className="flex flex-col gap-1.5">
          <h3 className="text-xs font-semibold text-neutral-700">GL entries this year not from costing, by source</h3>
          {sources.length === 0 ? <p className="text-xs text-neutral-400">None</p> : (
            <div className="w-fit overflow-x-auto rounded-md border border-neutral-200 bg-white">
              <table className="text-xs">
                <thead className={thCls}>
                  <tr>
                    <th className="px-3 py-1.5 text-left">Source</th>
                    <th className="px-3 py-1.5 text-right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map(([src, v]) => (
                    <tr key={src} className="border-t border-neutral-100">
                      <td className="px-3 py-1.5 font-mono">{src}</td>
                      <td className={subNumCls}>{formatMoney(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section className="flex flex-col gap-1.5">
        <h3 className="text-xs font-semibold text-neutral-700">Batches</h3>
        {!r.batch_expandable ? (
          <p className="text-xs text-neutral-500">
            NC stopped tracking this material by batch{r.no_batch_since ? ` on ${r.no_batch_since}` : ''} — material level only.
          </p>
        ) : r.batches === undefined ? (
          <button type="button" onClick={onShowBatches}
            className="w-fit text-xs font-medium text-primary-700 underline hover:text-primary-800">
            Show batches
          </button>
        ) : r.batches.length === 0 ? (
          <p className="text-xs text-neutral-400">No batch has a balance on this date.</p>
        ) : (
          <div className="max-h-96 overflow-auto rounded-md border border-neutral-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className={cn(thCls, 'sticky top-0')}>
                <tr>
                  <th className="px-3 py-1.5 text-left">Batch</th>
                  <th className="px-3 py-1.5 text-left">NC batches</th>
                  <th className="px-3 py-1.5 text-left">WMS lots</th>
                  <th className="px-3 py-1.5 text-left">Supplier batches</th>
                  <th className="px-3 py-1.5 text-left">Match</th>
                  <th className="px-3 py-1.5 text-right">WMS</th>
                  <th className="px-3 py-1.5 text-right">NC (WMS wh.)</th>
                  <th className="px-3 py-1.5 text-right">NC (other wh.)</th>
                  <th className="px-3 py-1.5 text-right">IA</th>
                  <th className="px-3 py-1.5 text-right">IC−WMS</th>
                  <th className="px-3 py-1.5" aria-label="Trace" />
                </tr>
              </thead>
              <tbody>
                {r.batches.map((b, i) => (
                  <tr key={`${b.label}-${i}`} className="border-t border-neutral-100">
                    <td className="px-3 py-1.5 font-mono">{b.label}</td>
                    <td className="px-3 py-1.5 font-mono">{b.nc_batches.join(', ') || '—'}</td>
                    <td className="px-3 py-1.5 font-mono">{b.wms_lots.join(', ') || '—'}</td>
                    <td className="px-3 py-1.5 font-mono">{b.supplier_batches.join(', ') || '—'}</td>
                    <td className="px-3 py-1.5"><MatchBadge match={b.match} /></td>
                    <td className={subNumCls}>{formatQty(b.wms)}</td>
                    <td className={subNumCls}>{formatQty(b.ic_wms)}</td>
                    <td className={subNumCls}>{formatQty(b.ic_other)}</td>
                    <td className={subNumCls}>{formatQty(b.ia)}</td>
                    <td className={cn(subNumCls,
                      absExceeds(b.diff_ic_wms, tolQty) ? 'font-medium text-danger-600' : 'text-neutral-400')}>
                      {formatQty(b.diff_ic_wms)}
                    </td>
                    <td className="px-3 py-1.5">
                      {/* Not for materials on the no-batch list: NC stopped
                          recording them by batch, and the API refuses it. */}
                      {!r.no_batch_since && <BatchTraceLink material={r.material_code} b={b} date={date} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

export function ReconRowView({ r, showAmt, tol, date, open, onToggle, onShowBatches, cols }: {
  r: ReconRow
  showAmt: boolean
  tol: { qty: string; amount: string }
  date: string
  open: boolean
  onToggle: () => void
  onShowBatches: () => void
  cols: number
}) {
  const elsewhere = sumTitle(r)
  return (
    <>
      <tr className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50', open && 'bg-neutral-50')}
        onClick={onToggle}>
        <td className="w-8 px-2 py-2 text-neutral-400">
          <button type="button" aria-expanded={open} aria-label={open ? 'Hide details' : 'Show details'}
            onClick={(e) => { e.stopPropagation(); onToggle() }}>
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        </td>
        <td className="px-3 py-2">
          <div className="flex flex-col gap-1">
            <span>
              <MaterialCell code={r.material_code} name={r.material_name} />
              {r.uom && <span className="ml-1.5 text-[11px] text-neutral-400">{r.uom}</span>}
            </span>
            <span className="flex flex-wrap gap-1">
              {!r.in_nc && <Badge variant="warning" className="text-[10px]">Not in NC</Badge>}
              {r.no_batch_since && <Badge variant="neutral" className="text-[10px]">NC stopped {r.no_batch_since}</Badge>}
              {r.unit_check === 'mismatch' && (
                <span title="Information only — quantities are still compared as recorded"
                  className="inline-flex rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-medium text-neutral-500">
                  units differ: NC {r.uom ?? '—'} / WMS {r.wms_uom ?? '—'}
                </span>
              )}
            </span>
          </div>
        </td>
        <Num v={r.wms} />
        <Num v={r.ic_wms} />
        <td className={cn(numCls, isZero(elsewhere.sum) && 'text-neutral-400')} title={elsewhere.title}>
          {formatQty(elsewhere.sum)}
        </td>
        <td className={cn(numCls, isNegative(r.ic_total) && 'text-danger-600')}
          title={isZero(r.ic_transient) ? undefined : `Includes ${formatQty(r.ic_transient)} in transient / other / unclassified warehouses`}>
          {formatQty(r.ic_total)}
        </td>
        <Num v={r.ia_qty} />
        <Num v={r.gl_qty} />
        <DiffCell diff={r.diff.ic_wms} unexplained={r.unexplained.ic_wms} tol={tol.qty} />
        <DiffCell diff={r.diff.ia_ic} unexplained={r.unexplained.ia_ic} tol={tol.qty} />
        <DiffCell diff={r.diff.gl_ia_qty} unexplained={r.unexplained.gl_ia_qty} tol={tol.qty} />
        {showAmt && <Num v={r.ia_amount} money />}
        {showAmt && <Num v={r.gl_amount} money />}
        {showAmt && <DiffCell diff={r.diff.gl_ia_amount} unexplained={r.unexplained.gl_ia_amount} tol={tol.amount} money />}
        <td className="px-3 py-2"><StatusBadge status={r.status} /></td>
        <td className="px-3 py-2">
          <span className="flex flex-wrap gap-1">
            {r.categories.map((c, i) => <CauseBadge key={`${c.code}-${i}`} c={c} showAmt={showAmt} />)}
          </span>
        </td>
        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
          <TraceLink material={r.material_code} date={date}
            className="inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:underline">
            <Route aria-hidden className="h-3.5 w-3.5" />Trace
          </TraceLink>
        </td>
      </tr>
      {open && (
        <tr className="border-t border-neutral-100 bg-neutral-50/60">
          <td colSpan={cols} className="px-4 py-3">
            <Expansion r={r} showAmt={showAmt} tolQty={tol.qty} date={date} onShowBatches={onShowBatches} />
          </td>
        </tr>
      )}
    </>
  )
}

/** external + production as a decimal string, summed on the digits. */
function sumTitle(r: ReconRow): { sum: string; title: string } {
  return {
    sum: addDecimal(r.ic_external, r.ic_production),
    title: `External: ${formatQty(r.ic_external)} · Production line-side: ${formatQty(r.ic_production)}`,
  }
}

export function GlExtraTable({ title, hint, rows, showAmt }: {
  title: string
  hint: string
  rows: ReconGlExtra[]
  showAmt: boolean
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <h2 className="text-sm font-semibold text-neutral-800">{title}</h2>
      <p className="text-[11px] text-neutral-400">{hint}</p>
      {rows.length === 0 ? <p className="text-xs text-neutral-400">None</p> : (
        <div className="w-fit max-w-full overflow-x-auto rounded-md border border-neutral-200 bg-white">
          <table className="text-xs">
            <thead className={thCls}>
              <tr>
                <th className="px-3 py-1.5 text-left">Account</th>
                <th className="px-3 py-1.5 text-left">Name</th>
                <th className="px-3 py-1.5 text-right">Qty</th>
                {showAmt && <th className="px-3 py-1.5 text-right">Amount</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => (
                <tr key={g.account_code} className="border-t border-neutral-100">
                  <td className="px-3 py-1.5 font-mono">{g.account_code}</td>
                  <td className="px-3 py-1.5">{g.account_name ?? '—'}</td>
                  <td className={subNumCls}>{formatQty(g.qty)}</td>
                  {showAmt && <td className={subNumCls}>{formatMoney(g.amount)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
