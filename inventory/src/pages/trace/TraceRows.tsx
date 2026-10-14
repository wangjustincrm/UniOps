// Trace page pieces: the category summary and the two-sided movement table.
import { Fragment, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { addDecimal, formatQty, isNegative, isZero } from '@/lib/number'
import { thCls } from '@/pages/nc/NcUi'
import { lineDetail } from './exportTrace'
import {
  formatTraceTime, rowHidden, signedQty,
  type TraceLine, type TraceResponse, type TraceRow, type TraceSummaryLine,
} from './traceApi'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

const numCls = 'text-right font-mono text-xs tabular-nums whitespace-nowrap'

function qtyTone(v: string | null | undefined): string {
  if (isZero(v)) return 'text-neutral-400'
  return isNegative(v) ? 'text-danger-600' : 'text-success-700'
}

function diffTone(v: string | null | undefined): string {
  return isZero(v) ? 'text-neutral-400' : 'font-medium text-danger-600'
}

// ── Category summary ───────────────────────────────────────────────────────

export function SummaryTable({ t, showHidden }: { t: TraceResponse; showHidden: boolean }) {
  const lines: TraceSummaryLine[] = t.summary.filter((s) => showHidden || !s.hidden_by_default)
  const hiddenCount = t.summary.length - lines.length
  return (
    <div className="w-fit max-w-full overflow-x-auto rounded-lg border border-neutral-200 bg-white">
      <table className="text-sm">
        <thead className={thCls}>
          <tr>
            <th className="px-3 py-2 text-left">Category</th>
            <th className="px-3 py-2 text-right">NC</th>
            <th className="px-3 py-2 text-right">WMS</th>
            <th className="px-3 py-2 text-right">Difference</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-t border-neutral-100 bg-neutral-50/60">
            <td className="px-3 py-1.5 text-xs text-neutral-600">Opening (before {t.start})</td>
            <td className={cn('px-3 py-1.5', numCls)}>{formatQty(t.opening.nc)}</td>
            <td className={cn('px-3 py-1.5', numCls)}>{formatQty(t.opening.wms)}</td>
            <td className={cn('px-3 py-1.5', numCls, diffTone(openingDiff(t)))}>{formatQty(openingDiff(t))}</td>
          </tr>
          {lines.map((s) => (
            <tr key={s.category} className={cn('border-t border-neutral-100', s.hidden_by_default && 'text-neutral-400')}>
              <td className="px-3 py-1.5 text-xs">{s.label}</td>
              <td className={cn('px-3 py-1.5', numCls, qtyTone(s.nc))}>{signedQty(s.nc)}</td>
              <td className={cn('px-3 py-1.5', numCls, qtyTone(s.wms))}>{signedQty(s.wms)}</td>
              <td className={cn('px-3 py-1.5', numCls, diffTone(s.diff))}>{formatQty(s.diff)}</td>
            </tr>
          ))}
          {lines.length === 0 && (
            <tr className="border-t border-neutral-100">
              <td colSpan={4} className="px-3 py-3 text-center text-xs text-neutral-400">No movements in the window.</td>
            </tr>
          )}
          <tr className="border-t border-neutral-200 bg-neutral-50/60 font-medium">
            <td className="px-3 py-1.5 text-xs text-neutral-700">Closing ({t.date})</td>
            <td className={cn('px-3 py-1.5', numCls)}>{formatQty(t.closing.nc)}</td>
            <td className={cn('px-3 py-1.5', numCls)}>{formatQty(t.closing.wms)}</td>
            <td className={cn('px-3 py-1.5', numCls, diffTone(t.closing.diff))}>{formatQty(t.closing.diff)}</td>
          </tr>
        </tbody>
      </table>
      {hiddenCount > 0 && (
        <p className="border-t border-neutral-100 px-3 py-1.5 text-[11px] text-neutral-400">
          {hiddenCount} internal / state-change {hiddenCount === 1 ? 'category' : 'categories'} hidden
        </p>
      )}
    </div>
  )
}

/** NC opening − WMS opening, on the digits (WMS starts from zero). */
function openingDiff(t: TraceResponse): string {
  const w = t.opening.wms.trim()
  return addDecimal(t.opening.nc, w.startsWith('-') ? w.slice(1) : `-${w}`)
}

// ── Movements ──────────────────────────────────────────────────────────────

const sideGrid = 'grid grid-cols-[minmax(9rem,1fr)_6.5rem_7.5rem] items-baseline gap-x-2'

function SideHeader({ label }: { label: string }) {
  return (
    <div className={sideGrid}>
      <span>{label} document</span>
      <span className="text-right">Qty</span>
      <span className="text-right">Balance</span>
    </div>
  )
}

function LineBadges({ ln, kind }: { ln: TraceLine; kind: TraceRow['kind'] }) {
  return (
    <>
      {ln.side === 'nc' && ln.unsigned && (
        <span className="ml-1 rounded-full bg-warning-50 px-1.5 text-[10px] font-medium text-warning-800"
          title="NC document not signed yet">unsigned</span>
      )}
      {ln.side === 'wms' && ln.category === 'manual_opening' && (
        <span className="ml-1 rounded-full bg-neutral-100 px-1.5 text-[10px] font-medium text-neutral-500"
          title="WMS's own opening stock, imported by hand at go-live">
          {kind === 'wms_only' ? 'manual opening (not matched to NC)' : 'manual opening'}
        </span>
      )}
    </>
  )
}

function SideCell({ lines, total, kind }: { lines: TraceLine[]; total: string; kind: TraceRow['kind'] }) {
  if (lines.length === 0) return <span className="text-xs text-neutral-300">—</span>
  return (
    <div className="flex flex-col gap-0.5">
      {lines.map((ln) => (
        <div key={ln.id} className={sideGrid}>
          <span className="min-w-0 truncate font-mono text-xs" title={lineDetail(ln) || undefined}>
            {ln.doc ?? <span className="font-sans italic text-neutral-400">no document</span>}
            {kind === 'pair' && (
              <span className="ml-1.5 font-sans text-[10px] text-neutral-400">{formatTraceTime(ln.time, ln.date_only)}</span>
            )}
            <LineBadges ln={ln} kind={kind} />
          </span>
          <span className={cn(numCls, qtyTone(ln.qty))}>{signedQty(ln.qty)}</span>
          <span className={cn(numCls, 'text-neutral-600')}>{formatQty(ln.balance)}</span>
        </div>
      ))}
      {lines.length > 1 && (
        <div className={cn(sideGrid, 'border-t border-dashed border-neutral-200 pt-0.5')}>
          <span className="text-[11px] text-neutral-500">{lines.length} lines, total</span>
          <span className={cn(numCls, 'font-medium', qtyTone(total))}>{signedQty(total)}</span>
          <span />
        </div>
      )}
    </div>
  )
}

function DetailList({ lines, side }: { lines: TraceLine[]; side: 'NC' | 'WMS' }) {
  if (lines.length === 0) return <p className="text-xs text-neutral-400">No {side} movement.</p>
  return (
    <ul className="flex flex-col gap-1">
      {lines.map((ln) => (
        <li key={ln.id} className="text-xs text-neutral-600">
          <span className="font-mono text-neutral-800">{ln.doc ?? '(no document)'}</span>
          <span className="ml-1.5 text-neutral-400">{formatTraceTime(ln.time, ln.date_only)}</span>
          <span className="ml-1.5 text-neutral-500">{ln.category_label}</span>
          <div className="text-neutral-500">{lineDetail(ln) || '—'}</div>
          {ln.side === 'nc' && ln.nc_time && (
            <div className="text-[11px] text-neutral-400">NC business date {ln.nc_time.slice(0, 10)}</div>
          )}
        </li>
      ))}
    </ul>
  )
}

function MatchCell({ r }: { r: TraceRow }) {
  if (r.kind === 'nc_only') return <span className="rounded-full bg-danger-50 px-1.5 text-[10px] font-medium text-danger-700">NC only</span>
  if (r.kind === 'wms_only') return <span className="rounded-full bg-danger-50 px-1.5 text-[10px] font-medium text-danger-700">WMS only</span>
  if (r.tier === 1) {
    return (
      <span className="rounded-full bg-success-50 px-1.5 font-mono text-[10px] font-medium text-success-700"
        title="Tier 1 — paired by a document number both sides carry">doc {r.key}</span>
    )
  }
  return (
    <span className="rounded-full bg-primary-50 px-1.5 text-[10px] font-medium text-primary-700"
      title="Tier 2 — same category, same quantity, close in time">qty+time</span>
  )
}

export function MovementsTable({ rows, indices, highlight, registerRow }: {
  rows: TraceRow[]
  /** Indices into `rows` to show (after the hidden filter). */
  indices: number[]
  highlight: number | null
  registerRow: (index: number, el: HTMLTableRowElement | null) => void
}) {
  const [open, setOpen] = useState<Set<number>>(() => new Set())
  function toggle(i: number) {
    setOpen((cur) => {
      const next = new Set(cur)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }
  return (
    <ScrollTable>
      <table className="min-w-full text-sm">
        <thead className={stickyHeadCls}>
          <tr>
            <th className="px-3 py-2 text-left font-medium"><SideHeader label="NC" /></th>
            <th className="border-x border-neutral-200 px-3 py-2 text-left">Time · category · match</th>
            <th className="px-3 py-2 text-left font-medium"><SideHeader label="WMS" /></th>
          </tr>
        </thead>
        <tbody>
          {indices.length === 0 && (
            <tr><td colSpan={3} className="px-3 py-8 text-center text-sm text-neutral-400">No movements in the window.</td></tr>
          )}
          {indices.map((i) => {
            const r = rows[i]
            const isOpen = open.has(i)
            const onlyDateNc = r.kind === 'nc_only' && r.nc[0]?.date_only
            return (
              <Fragment key={i}>
                <tr ref={(el) => registerRow(i, el)}
                  className={cn('border-t border-neutral-100 align-top',
                    r.kind !== 'pair' && 'bg-danger-50/50',
                    rowHidden(r) && 'text-neutral-400',
                    highlight === i && 'outline outline-2 -outline-offset-2 outline-danger-500')}>
                  <td className="px-3 py-1.5"><SideCell lines={r.nc} total={r.nc_qty} kind={r.kind} /></td>
                  <td className="border-x border-neutral-100 px-3 py-1.5">
                    <div className="flex items-start gap-1.5">
                      <button type="button" onClick={() => toggle(i)} aria-expanded={isOpen}
                        aria-label={isOpen ? 'Hide details' : 'Show details'}
                        className="mt-0.5 text-neutral-400 hover:text-neutral-700">
                        {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                      </button>
                      <div className="flex min-w-[11rem] flex-col gap-0.5">
                        <span className="font-mono text-[11px] text-neutral-600">{formatTraceTime(r.time, onlyDateNc)}</span>
                        <span className="text-xs">{(r.nc[0] ?? r.wms[0])?.category_label ?? r.category}</span>
                        <span className="flex flex-wrap items-center gap-1">
                          <MatchCell r={r} />
                          {r.kind === 'pair' && !isZero(r.diff) && (
                            <span className={cn('font-mono text-[11px]', diffTone(r.diff))} title="NC − WMS for this group">
                              diff {formatQty(r.diff)}
                            </span>
                          )}
                        </span>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-1.5"><SideCell lines={r.wms} total={r.wms_qty} kind={r.kind} /></td>
                </tr>
                {isOpen && (
                  <tr className="border-t border-neutral-100 bg-neutral-50/60">
                    <td className="px-4 py-2"><DetailList lines={r.nc} side="NC" /></td>
                    <td className="border-x border-neutral-100 px-4 py-2 text-[11px] text-neutral-500">
                      {r.kind === 'pair'
                        ? <>Group: NC {signedQty(r.nc_qty)} · WMS {signedQty(r.wms_qty)} · diff <span className={diffTone(r.diff)}>{formatQty(r.diff)}</span></>
                        : 'Unmatched — no movement on the other side was paired with this one.'}
                    </td>
                    <td className="px-4 py-2"><DetailList lines={r.wms} side="WMS" /></td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </ScrollTable>
  )
}
