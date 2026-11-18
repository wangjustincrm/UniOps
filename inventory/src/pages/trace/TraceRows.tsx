// Trace page pieces: the category summary and the two-sided movement table.
import { Fragment, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn, formatDate } from '@/lib/utils'
import { addDecimal, formatQty, isNegative, isZero } from '@/lib/number'
import { thCls } from '@/pages/nc/NcUi'
import { lineDetail } from './exportTrace'
import {
  formatTraceTimeDisplay, rowHidden, signedQty,
  type TraceLine, type TraceResponse, type TraceRow, type TraceSummaryLine,
} from './traceApi'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

const numCls = 'text-right font-mono text-xs tabular-nums whitespace-nowrap'

function qtyTone(v: string | null | undefined): string {
  if (isZero(v)) return 'text-fg-muted'
  return isNegative(v) ? 'text-danger-fg' : 'text-success-fg'
}

function diffTone(v: string | null | undefined): string {
  return isZero(v) ? 'text-fg-muted' : 'font-medium text-danger-fg'
}

// ── Category summary ───────────────────────────────────────────────────────

export function SummaryTable({ t, showHidden }: { t: TraceResponse; showHidden: boolean }) {
  const lines: TraceSummaryLine[] = t.summary.filter((s) => showHidden || !s.hidden_by_default)
  const hiddenCount = t.summary.length - lines.length
  return (
    <div className="w-fit max-w-full overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="text-sm">
        <thead className={thCls}>
          <tr>
            <th className="px-3 py-2 text-left">Category</th>
            <th className={cn('px-3 py-2 text-right', NC_HEAD)}>NC</th>
            <th className={cn('px-3 py-2 text-right', WMS_HEAD)}>WMS</th>
            <th className="px-3 py-2 text-right">Difference</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-t border-line-subtle bg-surface-subtle/60">
            <td className="px-3 py-1.5 text-xs text-fg-secondary">Opening (before {formatDate(t.start)})</td>
            <td className={cn('px-3 py-1.5', numCls, NC_CELL)}>{formatQty(t.opening.nc)}</td>
            <td className={cn('px-3 py-1.5', numCls, WMS_CELL)}>{formatQty(t.opening.wms)}</td>
            <td className={cn('px-3 py-1.5', numCls, diffTone(openingDiff(t)))}>{formatQty(openingDiff(t))}</td>
          </tr>
          {lines.map((s) => (
            <tr key={s.category} className={cn('border-t border-line-subtle', s.hidden_by_default && 'text-fg-muted')}>
              <td className="px-3 py-1.5 text-xs">{s.label}</td>
              <td className={cn('px-3 py-1.5', numCls, NC_CELL, qtyTone(s.nc))}>{signedQty(s.nc)}</td>
              <td className={cn('px-3 py-1.5', numCls, WMS_CELL, qtyTone(s.wms))}>{signedQty(s.wms)}</td>
              <td className={cn('px-3 py-1.5', numCls, diffTone(s.diff))}>{formatQty(s.diff)}</td>
            </tr>
          ))}
          {lines.length === 0 && (
            <tr className="border-t border-line-subtle">
              <td colSpan={4} className="px-3 py-3 text-center text-xs text-fg-muted">No movements in the window.</td>
            </tr>
          )}
          <tr className="border-t border-line bg-surface-subtle/60 font-medium">
            <td className="px-3 py-1.5 text-xs text-fg-secondary">Closing ({formatDate(t.date)})</td>
            <td className={cn('px-3 py-1.5', numCls, NC_CELL)}>{formatQty(t.closing.nc)}</td>
            <td className={cn('px-3 py-1.5', numCls, WMS_CELL)}>{formatQty(t.closing.wms)}</td>
            <td className={cn('px-3 py-1.5', numCls, diffTone(t.closing.diff))}>{formatQty(t.closing.diff)}</td>
          </tr>
        </tbody>
      </table>
      {hiddenCount > 0 && (
        <p className="border-t border-line-subtle px-3 py-1.5 text-2xs text-fg-muted">
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

function SideHeader({ label, closing, date }: { label: string; closing: string; date: string }) {
  // The closing balance sits in the (sticky) header so it is visible without
  // scrolling to the last movement (user request 2026-10-14).
  return (
    <div className={sideGrid}>
      <span>{label} document</span>
      <span className="text-right">Qty</span>
      <span className="flex flex-col items-end">
        <span>Balance</span>
        <span className={cn(numCls, 'text-sm font-semibold normal-case tracking-normal')}
          title={`${label} balance at the end of ${formatDate(date)}`}>
          {formatQty(closing)}
        </span>
      </span>
    </div>
  )
}

function LineBadges({ ln, kind }: { ln: TraceLine; kind: TraceRow['kind'] }) {
  return (
    <>
      {ln.side === 'nc' && ln.unsigned && (
        <span className="ml-1 rounded-full bg-warning-soft px-1.5 text-2xs font-medium text-warning-fg"
          title="NC document not signed yet">unsigned</span>
      )}
      {ln.side === 'wms' && ln.category === 'manual_opening' && (
        <span className="ml-1 rounded-full bg-surface-sunken px-1.5 text-2xs font-medium text-fg-muted"
          title="WMS's own opening stock, imported by hand at go-live">
          {kind === 'wms_only' ? 'manual opening (not matched to NC)' : 'manual opening'}
        </span>
      )}
    </>
  )
}

// Each side keeps its own tint everywhere on the page so NC and WMS can be
// told apart at a glance (user request 2026-10-14). Neither colour is a
// status colour: red/green/amber keep meaning "unmatched / ok / warning".
const NC_HEAD = 'bg-info-soft text-info-fg'
const NC_CELL = 'bg-info-soft/70'
const WMS_HEAD = 'bg-cat4-soft text-cat4-fg'
const WMS_CELL = 'bg-cat4-soft/70'

function SideCell({ lines, total, kind }: { lines: TraceLine[]; total: string; kind: TraceRow['kind'] }) {
  if (lines.length === 0) return <span className="text-xs text-fg-faint">—</span>
  return (
    <div className="flex flex-col gap-0.5">
      {lines.map((ln) => (
        <div key={ln.id} className={sideGrid}>
          <span className="min-w-0 truncate font-mono text-xs" title={lineDetail(ln) || undefined}>
            {ln.doc ?? <span className="font-sans italic text-fg-muted">no document</span>}
            {kind === 'pair' && (
              <span className="ml-1.5 font-sans text-2xs text-fg-muted">{formatTraceTimeDisplay(ln.time, ln.date_only)}</span>
            )}
            <LineBadges ln={ln} kind={kind} />
          </span>
          <span className={cn(numCls, qtyTone(ln.qty))}>{signedQty(ln.qty)}</span>
          <span className={cn(numCls, 'text-fg-secondary')}>{formatQty(ln.balance)}</span>
        </div>
      ))}
      {lines.length > 1 && (
        <div className={cn(sideGrid, 'border-t border-dashed border-line pt-0.5')}>
          <span className="text-2xs text-fg-muted">{lines.length} lines, total</span>
          <span className={cn(numCls, 'font-medium', qtyTone(total))}>{signedQty(total)}</span>
          <span />
        </div>
      )}
    </div>
  )
}

function DetailList({ lines, side }: { lines: TraceLine[]; side: 'NC' | 'WMS' }) {
  if (lines.length === 0) return <p className="text-xs text-fg-muted">No {side} movement.</p>
  return (
    <ul className="flex flex-col gap-1">
      {lines.map((ln) => (
        <li key={ln.id} className="text-xs text-fg-secondary">
          <span className="font-mono text-fg">{ln.doc ?? '(no document)'}</span>
          <span className="ml-1.5 text-fg-muted">{formatTraceTimeDisplay(ln.time, ln.date_only)}</span>
          <span className="ml-1.5 text-fg-muted">{ln.category_label}</span>
          <div className="text-fg-muted">{lineDetail(ln) || '—'}</div>
          {ln.side === 'nc' && ln.nc_time && (
            <div className="text-2xs text-fg-muted">NC business date {formatDate(ln.nc_time.slice(0, 10))}</div>
          )}
        </li>
      ))}
    </ul>
  )
}

function MatchCell({ r }: { r: TraceRow }) {
  if (r.kind === 'nc_only') return <span className="rounded-full bg-danger-soft px-1.5 text-2xs font-medium text-danger-fg">NC only</span>
  if (r.kind === 'wms_only') return <span className="rounded-full bg-danger-soft px-1.5 text-2xs font-medium text-danger-fg">WMS only</span>
  if (r.tier === 1) {
    return (
      <span className="rounded-full bg-success-soft px-1.5 font-mono text-2xs font-medium text-success-fg"
        title="Tier 1 — paired by a document number both sides carry">doc {r.key}</span>
    )
  }
  return (
    <span className="rounded-full bg-primary-50 px-1.5 text-2xs font-medium text-primary-700"
      title="Tier 2 — same category, same quantity, close in time">qty+time</span>
  )
}

export function MovementsTable({ rows, indices, highlight, registerRow, closing, date }: {
  rows: TraceRow[]
  /** End-of-window balances, shown in the header. */
  closing: { nc: string; wms: string; diff: string }
  date: string
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
            <th className={cn('px-3 py-2 text-left font-medium', NC_HEAD)}><SideHeader label="NC" closing={closing.nc} date={date} /></th>
            <th className="border-x border-line px-3 py-2 text-left">
              <span className="flex flex-col">
                <span>Time · category · match</span>
                <span className={cn(numCls, 'text-sm font-semibold normal-case tracking-normal', diffTone(closing.diff))}
                  title="NC − WMS at the end of the window">
                  End difference {formatQty(closing.diff)}
                </span>
              </span>
            </th>
            <th className={cn('px-3 py-2 text-left font-medium', WMS_HEAD)}><SideHeader label="WMS" closing={closing.wms} date={date} /></th>
          </tr>
        </thead>
        <tbody>
          {indices.length === 0 && (
            <tr><td colSpan={3} className="px-3 py-8 text-center text-sm text-fg-muted">No movements in the window.</td></tr>
          )}
          {indices.map((i) => {
            const r = rows[i]
            const isOpen = open.has(i)
            const onlyDateNc = r.kind === 'nc_only' && r.nc[0]?.date_only
            return (
              <Fragment key={i}>
                <tr ref={(el) => registerRow(i, el)}
                  className={cn('border-t border-line-subtle align-top',
                    rowHidden(r) && 'text-fg-muted',
                    highlight === i && 'outline outline-2 -outline-offset-2 outline-danger-solid')}>
                  <td className={cn('px-3 py-1.5', NC_CELL, r.kind === 'nc_only' && 'border-l-4 border-l-danger-400')}>
                    <SideCell lines={r.nc} total={r.nc_qty} kind={r.kind} />
                  </td>
                  <td className={cn('border-x border-line-subtle px-3 py-1.5', r.kind !== 'pair' && 'bg-danger-soft')}>
                    <div className="flex items-start gap-1.5">
                      <button type="button" onClick={() => toggle(i)} aria-expanded={isOpen}
                        aria-label={isOpen ? 'Hide details' : 'Show details'}
                        className="mt-0.5 text-fg-muted hover:text-fg-secondary">
                        {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                      </button>
                      <div className="flex min-w-[11rem] flex-col gap-0.5">
                        <span className="font-mono text-2xs text-fg-secondary">{formatTraceTimeDisplay(r.time, onlyDateNc)}</span>
                        <span className="text-xs">{(r.nc[0] ?? r.wms[0])?.category_label ?? r.category}</span>
                        <span className="flex flex-wrap items-center gap-1">
                          <MatchCell r={r} />
                          {r.kind === 'pair' && !isZero(r.diff) && (
                            <span className={cn('font-mono text-2xs', diffTone(r.diff))} title="NC − WMS for this group">
                              diff {formatQty(r.diff)}
                            </span>
                          )}
                        </span>
                      </div>
                    </div>
                  </td>
                  <td className={cn('px-3 py-1.5', WMS_CELL, r.kind === 'wms_only' && 'border-l-4 border-l-danger-400')}>
                    <SideCell lines={r.wms} total={r.wms_qty} kind={r.kind} />
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-t border-line-subtle bg-surface-subtle/60">
                    <td className={cn('px-4 py-2', NC_CELL)}><DetailList lines={r.nc} side="NC" /></td>
                    <td className="border-x border-line-subtle px-4 py-2 text-2xs text-fg-muted">
                      {r.kind === 'pair'
                        ? <>Group: NC {signedQty(r.nc_qty)} · WMS {signedQty(r.wms_qty)} · diff <span className={diffTone(r.diff)}>{formatQty(r.diff)}</span></>
                        : 'Unmatched — no movement on the other side was paired with this one.'}
                    </td>
                    <td className={cn('px-4 py-2', WMS_CELL)}><DetailList lines={r.wms} side="WMS" /></td>
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
