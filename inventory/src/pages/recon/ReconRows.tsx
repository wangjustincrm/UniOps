// Reconciliation table pieces: one material row, its expansion (causes, GL by
// source, batch groups), and the small GL tables under the main table.
import { ChevronDown, ChevronRight, Route } from 'lucide-react'
import { Badge, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { cn, formatDate } from '@/lib/utils'
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
    <Badge size="sm" title={title} variant={c.code === 'batch_label' ? 'cat4' : 'neutral'}>
      {shortLabel(c.code, c.label)}
    </Badge>
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
    <Td className={cn(numCls, diff != null && isZero(diff) && 'text-fg-faint', bad && 'text-danger-fg font-medium')}>
      {fmt(diff)}
      {bad && <div className="text-2xs font-normal">unexplained {fmt(unexplained)}</div>}
    </Td>
  )
}

function Num({ v, money = false, uom }: { v: string | null; money?: boolean; uom?: string | null }) {
  return (
    <Td className={cn(numCls, isNegative(v) && 'text-danger-fg', v != null && isZero(v) && 'text-fg-muted')}>
      {money ? formatMoney(v) : formatQty(v)}{uom !== undefined && v != null && <Uom uom={uom} />}
    </Td>
  )
}

export function MatchBadge({ match }: { match: ReconBatchGroup['match'] }) {
  if (match === 'both') return <Badge variant="success" size="sm">Both</Badge>
  if (match === 'wms_only') return <Badge variant="warning" size="sm">WMS only</Badge>
  return <Badge variant="warning" size="sm">NC only</Badge>
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
        <h3 className="text-xs font-semibold text-fg-secondary">Causes</h3>
        {r.categories.length === 0 ? <p className="text-xs text-fg-muted">None — no cause rule applied to this material.</p> : (
          <div className="overflow-x-auto rounded-md border border-line bg-surface">
            <Table density="compact" className="min-w-full">
              <THead className={thCls}>
                <Tr>
                  <Th>Cause</Th>
                  <Th>Side</Th>
                  <Th align="right">Qty</Th>
                  {showAmt && <Th align="right">Amount</Th>}
                </Tr>
              </THead>
              <TBody>
                {r.categories.map((c, i) => (
                  <Tr key={`${c.code}-${i}`}>
                    <Td>{c.label}</Td>
                    <Td className="whitespace-nowrap">{SIDE_LABELS[c.side]}</Td>
                    <Td className={subNumCls}>{formatQty(c.qty)}</Td>
                    {showAmt && <Td className={subNumCls}>{c.amount != null ? formatMoney(c.amount) : '—'}</Td>}
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </section>

      {showAmt && r.gl_by_source && (
        <section className="flex flex-col gap-1.5">
          <h3 className="text-xs font-semibold text-fg-secondary">GL entries this year not from costing, by source</h3>
          {sources.length === 0 ? <p className="text-xs text-fg-muted">None</p> : (
            <div className="w-fit overflow-x-auto rounded-md border border-line bg-surface">
              <Table density="compact">
                <THead className={thCls}>
                  <Tr>
                    <Th>Source</Th>
                    <Th align="right">Amount</Th>
                  </Tr>
                </THead>
                <TBody>
                  {sources.map(([src, v]) => (
                    <Tr key={src}>
                      <Td className="font-mono">{src}</Td>
                      <Td className={subNumCls}>{formatMoney(v)}</Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </section>
      )}

      <section className="flex flex-col gap-1.5">
        <h3 className="text-xs font-semibold text-fg-secondary">Batches</h3>
        {!r.batch_expandable ? (
          <p className="text-xs text-fg-muted">
            NC stopped tracking this material by batch{r.no_batch_since ? ` on ${formatDate(r.no_batch_since)}` : ''} — material level only.
          </p>
        ) : r.batches === undefined ? (
          <Button variant="link" type="button" onClick={onShowBatches}
            className="text-xs">
            Show batches
          </Button>
        ) : r.batches.length === 0 ? (
          <p className="text-xs text-fg-muted">No batch has a balance on this date.</p>
        ) : (
          <div className="max-h-96 overflow-auto rounded-md border border-line bg-surface">
            <Table density="compact" className="min-w-full">
              <THead className={cn(thCls, 'sticky top-0')}>
                <Tr>
                  <Th>Batch</Th>
                  <Th>NC batches</Th>
                  <Th>WMS lots</Th>
                  <Th>Supplier batches</Th>
                  <Th>Match</Th>
                  <Th align="right">WMS</Th>
                  <Th align="right">NC (WMS wh.)</Th>
                  <Th align="right">NC (other wh.)</Th>
                  <Th align="right">IA</Th>
                  <Th align="right">IC−WMS</Th>
                  <Th aria-label="Trace" />
                </Tr>
              </THead>
              <TBody>
                {r.batches.map((b, i) => (
                  <Tr key={`${b.label}-${i}`}>
                    <Td className="font-mono">{b.label}</Td>
                    <Td className="font-mono">{b.nc_batches.join(', ') || '—'}</Td>
                    <Td className="font-mono">{b.wms_lots.join(', ') || '—'}</Td>
                    <Td className="font-mono">{b.supplier_batches.join(', ') || '—'}</Td>
                    <Td><MatchBadge match={b.match} /></Td>
                    <Td className={subNumCls}>{formatQty(b.wms)}</Td>
                    <Td className={subNumCls}>{formatQty(b.ic_wms)}</Td>
                    <Td className={subNumCls}>{formatQty(b.ic_other)}</Td>
                    <Td className={subNumCls}>{formatQty(b.ia)}</Td>
                    <Td className={cn(subNumCls,
                      absExceeds(b.diff_ic_wms, tolQty) ? 'font-medium text-danger-fg' : 'text-fg-muted')}>
                      {formatQty(b.diff_ic_wms)}
                    </Td>
                    <Td>
                      {/* Not for materials on the no-batch list: NC stopped
                          recording them by batch, and the API refuses it. */}
                      {!r.no_batch_since && <BatchTraceLink material={r.material_code} b={b} date={date} />}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
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
      <Tr className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-subtle', open && 'bg-surface-subtle')}
        onClick={onToggle}>
        <Td className="w-8 text-fg-muted">
          <Button variant="subtle" size="icon-xs" type="button" aria-expanded={open} aria-label={open ? 'Hide details' : 'Show details'}
            onClick={(e) => { e.stopPropagation(); onToggle() }}>
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </Button>
        </Td>
        <Td>
          <div className="flex flex-col gap-1">
            <span>
              <MaterialCell code={r.material_code} name={r.material_name} />
              {r.uom && <span className="ml-1.5 text-2xs text-fg-muted">{r.uom}</span>}
            </span>
            <span className="flex flex-wrap gap-1">
              {!r.in_nc && <Badge variant="warning" size="sm">Not in NC</Badge>}
              {r.no_batch_since && <Badge size="sm">NC stopped {formatDate(r.no_batch_since)}</Badge>}
              {r.unit_check === 'mismatch' && (
                <Badge size="sm" title="Information only — quantities are still compared as recorded">
                  units differ: NC {r.uom ?? '—'} / WMS {r.wms_uom ?? '—'}
                </Badge>
              )}
            </span>
          </div>
        </Td>
        <Num v={r.wms} />
        <Num v={r.ic_wms} />
        <Td className={cn(numCls, isZero(elsewhere.sum) && 'text-fg-muted')} title={elsewhere.title}>
          {formatQty(elsewhere.sum)}
        </Td>
        <Td className={cn(numCls, isNegative(r.ic_total) && 'text-danger-fg')}
          title={isZero(r.ic_transient) ? undefined : `Includes ${formatQty(r.ic_transient)} in transient / other / unclassified warehouses`}>
          {formatQty(r.ic_total)}
        </Td>
        <Num v={r.ia_qty} />
        <Num v={r.gl_qty} />
        <DiffCell diff={r.diff.ic_wms} unexplained={r.unexplained.ic_wms} tol={tol.qty} />
        <DiffCell diff={r.diff.ia_ic} unexplained={r.unexplained.ia_ic} tol={tol.qty} />
        <DiffCell diff={r.diff.gl_ia_qty} unexplained={r.unexplained.gl_ia_qty} tol={tol.qty} />
        {showAmt && <Num v={r.ia_amount} money />}
        {showAmt && <Num v={r.gl_amount} money />}
        {showAmt && <DiffCell diff={r.diff.gl_ia_amount} unexplained={r.unexplained.gl_ia_amount} tol={tol.amount} money />}
        <Td><StatusBadge status={r.status} /></Td>
        <Td>
          <span className="flex flex-wrap gap-1">
            {r.categories.map((c, i) => <CauseBadge key={`${c.code}-${i}`} c={c} showAmt={showAmt} />)}
          </span>
        </Td>
        <Td onClick={(e) => e.stopPropagation()}>
          <TraceLink material={r.material_code} date={date}
            className="inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:underline">
            <Route aria-hidden className="h-3.5 w-3.5" />Trace
          </TraceLink>
        </Td>
      </Tr>
      {open && (
        <Tr className="bg-surface-subtle/60">
          <Td colSpan={cols}>
            <Expansion r={r} showAmt={showAmt} tolQty={tol.qty} date={date} onShowBatches={onShowBatches} />
          </Td>
        </Tr>
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
      <h2 className="text-sm font-semibold text-fg">{title}</h2>
      <p className="text-2xs text-fg-muted">{hint}</p>
      {rows.length === 0 ? <p className="text-xs text-fg-muted">None</p> : (
        <div className="w-fit max-w-full overflow-x-auto rounded-md border border-line bg-surface">
          <Table density="compact">
            <THead className={thCls}>
              <Tr>
                <Th>Account</Th>
                <Th>Name</Th>
                <Th align="right">Qty</Th>
                {showAmt && <Th align="right">Amount</Th>}
              </Tr>
            </THead>
            <TBody>
              {rows.map((g) => (
                <Tr key={g.account_code}>
                  <Td className="font-mono">{g.account_code}</Td>
                  <Td>{g.account_name ?? '—'}</Td>
                  <Td className={subNumCls}>{formatQty(g.qty)}</Td>
                  {showAmt && <Td className={subNumCls}>{formatMoney(g.amount)}</Td>}
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </section>
  )
}
