// Purchase Suggestions — Phase 1C's output.
//
// Sorted by ORDER DATE, not by material: the question a buyer opens this
// with is "what do I have to place today", and a list sorted by code makes
// them scan every row to find out.
//
// Three things are flagged per line rather than rolled into one "check
// this" marker, because each needs a different fix: no supplier (add one),
// no lead time (fill it in), order date already passed (expedite). The
// counts sit at the top so the state of the data is visible before anyone
// starts trusting the quantities.
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarClock, Download, Loader2, RefreshCw } from 'lucide-react'
import { Badge, Button, SortTh, useSortableRows, Checkbox, Select, TBody, THead, Table, Td, Tr } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { saveBlob } from '@/pages/forecast/forecastApi'
import { cn, formatDate, formatDateTime, todayISO, toLocalDate } from '@/lib/utils'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import {
  purchaseApi, type PurchaseLine, type PurchaseLineStatus,
} from './purchaseApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

/** " (18 days ago)" — how late, not just that it is late. A date on its own
 *  makes the reader do the subtraction, and the answer decides whether this
 *  is a phone call or a shrug. */
function daysAgo(isoDate: string): string {
  const then = new Date(`${isoDate}T00:00:00`)
  if (Number.isNaN(then.getTime())) return ''
  // Calendar days to the plant's today (company time zone), not elapsed time
  // from the browser's clock.
  const days = Math.round((toLocalDate(todayISO()).getTime() - then.getTime()) / 86400000)
  if (days <= 0) return ''
  return ` (${days} day${days === 1 ? '' : 's'} ago)`
}

function fmt(value: string): string {
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 3 }) : value
}

const STATUS_VARIANT: Record<PurchaseLineStatus, 'neutral' | 'success' | 'warning'> = {
  pending: 'warning', ordered: 'success', ignored: 'neutral',
}

export default function PurchaseSuggestionsPage() {
  const toasts = useToasts()
  const queryClient = useQueryClient()
  const permsQuery = usePermissions()
  const canExecute = !!permsQuery.data?.permissions['mrp.run.execute']

  const [runId, setRunId] = useState<string | null>(null)
  const [hideDone, setHideDone] = useState(true)

  const runsQuery = useQuery({ queryKey: ['purchase-runs'], queryFn: () => purchaseApi.list() })
  const runs = useMemo(() => runsQuery.data ?? [], [runsQuery.data])
  const activeId = runId ?? runs[0]?.id ?? null

  const runQuery = useQuery({
    queryKey: ['purchase-run', activeId],
    queryFn: () => purchaseApi.get(activeId as string),
    enabled: !!activeId,
  })
  const run = runQuery.data ?? null

  const generate = useMutation({
    mutationFn: () => purchaseApi.create(),
    onSuccess: async (created) => {
      setRunId(created.id)
      await queryClient.invalidateQueries({ queryKey: ['purchase-runs'] })
      toasts.success(`${created.run_no} — ${created.lines.length} suggestion(s).`)
    },
    onError: (err) => toasts.error(errMsg(err, 'Could not calculate purchase suggestions.')),
  })

  const setStatus = useMutation({
    mutationFn: ({ line, status }: { line: PurchaseLine; status: PurchaseLineStatus }) =>
      purchaseApi.setStatus(activeId as string, line.id, status),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['purchase-run', activeId] })
    },
    onError: (err) => toasts.error(errMsg(err, 'Could not update this line.')),
  })

  async function handleExport() {
    if (!activeId) return
    try {
      const { blob, filename } = await purchaseApi.exportRun(activeId)
      saveBlob(blob, filename, 'purchase-suggestions.xlsx')
    } catch (err) {
      toasts.error(errMsg(err, 'Could not export this calculation.'))
    }
  }

  const lines = useMemo(() => {
    const all = run?.lines ?? []
    return hideDone ? all.filter((l) => l.status === 'pending') : all
  }, [run, hideDone])
  // Client sort over the run's lines (one run is loaded whole). Rows keep
  // their identity by id, so a status change re-sorts without losing the row.
  const STATUS_LABEL: Record<PurchaseLineStatus, string> = { pending: 'Pending', ordered: 'Ordered', ignored: 'Ignored' }
  const { rows: sortedLines, sort, onSort } = useSortableRows(lines, {
    order_date: (l) => l.order_date,
    material: (l) => l.material_code,
    supplier: (l) => l.partner_code,
    need_week: (l) => l.need_week,
    gross: (l) => Number(l.gross_qty),
    available: (l) => Number(l.available_qty),
    net: (l) => Number(l.net_qty),
    suggested: (l) => Number(l.suggested_qty),
    status: (l) => STATUS_LABEL[l.status] ?? l.status,
  })

  const stats = run?.stats ?? null

  return (
    <div className="flex flex-col gap-4 p-4">
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />

      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-fg">Purchase Suggestions</h1>
          <p className="text-xs text-fg-muted">
            What to buy for the production plan currently in force, and when to order it.
            {run && <> Calculated {formatDateTime(run.created_at)} · {run.run_no}</>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {runs.length > 1 && (
            <Select
              value={activeId ?? ''}
              onChange={(e) => setRunId(e.target.value)}
             
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.run_no} · {formatDate(r.created_at)}
                </option>
              ))}
            </Select>
          )}
          {run && (
            <Button type="button" size="sm" variant="secondary" className="min-h-[44px]"
              onClick={() => { void handleExport() }}>
              <Download className="h-3.5 w-3.5" /> Export
            </Button>
          )}
          {canExecute && (
            <Button type="button" size="sm" className="min-h-[44px]"
              disabled={generate.isPending}
              onClick={() => generate.mutate()}>
              {generate.isPending
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <RefreshCw className="h-3.5 w-3.5" />}
              Calculate
            </Button>
          )}
        </div>
      </header>

      {stats && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-md border border-line bg-surface px-2 py-1">
            {stats.components} component(s) · {stats.lines} line(s)
          </span>
          {stats.products_without_bom > 0 && (
            <span className="rounded-md border border-warning-line bg-warning-soft px-2 py-1 text-warning-fg">
              {stats.products_without_bom} product(s) have no BOM — nothing was exploded for them
            </span>
          )}
          {stats.supplier_missing > 0 && (
            <span className="rounded-md border border-danger-line bg-danger-soft px-2 py-1 text-danger-fg">
              {stats.supplier_missing} line(s) have no supplier
            </span>
          )}
          {stats.lead_time_missing > 0 && (
            <span className="rounded-md border border-danger-line bg-danger-soft px-2 py-1 text-danger-fg">
              {stats.lead_time_missing} line(s) have no lead time — their order date is a guess
            </span>
          )}
          {stats.order_date_passed > 0 && (
            <span className="rounded-md border border-danger-line bg-danger-soft px-2 py-1 text-danger-fg">
              {stats.order_date_passed} line(s) should already have been ordered
            </span>
          )}
        </div>
      )}

      {run && (
        <label className="flex items-center gap-2 text-xs text-fg-secondary">
          <Checkbox checked={hideDone}
            onChange={(e) => setHideDone(e.target.checked)}
            />
          Show only lines still to deal with
        </label>
      )}

      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <Table density="compact" className="min-w-full">
          <THead>
            <Tr>
              <SortTh label="Order by" sortKey="order_date" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              <SortTh label="Material" sortKey="material" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              <SortTh label="Supplier" sortKey="supplier" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              <SortTh label="Needed" sortKey="need_week" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Gross" sortKey="gross" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Available" sortKey="available" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Net" sortKey="net" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Suggested" sortKey="suggested" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} align="center" className="px-3 py-2" />
            </Tr>
          </THead>
          <TBody>
            {runQuery.isLoading && (
              <Tr><Td align="center" colSpan={9} className="text-fg-muted">Loading…</Td></Tr>
            )}
            {!runQuery.isLoading && !run && (
              <Tr><Td align="center" colSpan={9} className="text-fg-muted">
                No calculation yet — press Calculate to work out what to buy for the plan in force.
              </Td></Tr>
            )}
            {run && lines.length === 0 && (
              <Tr><Td align="center" colSpan={9} className="text-fg-muted">
                {hideDone ? 'Everything here has been dealt with.' : 'This calculation produced no suggestions.'}
              </Td></Tr>
            )}
            {sortedLines.map((line) => (
              <Tr key={line.id} className={cn('border-t border-line-subtle',
                line.order_date_passed && 'bg-danger-50/40')}>
                <Td className="font-mono text-xs"
                  title={line.order_date_passed
                    ? `Overdue — this should have been ordered on ${formatDate(line.order_date)}`
                      + `${daysAgo(line.order_date)}. Ordered now, it arrives after it is `
                      + `needed in the week of ${formatDate(line.need_week)}.`
                    : `Order by ${formatDate(line.order_date)} to have it for the week of ${formatDate(line.need_week)}.`}>
                  <span className="flex items-center gap-1">
                    {line.order_date_passed && (
                      // Not aria-hidden: for a screen reader this icon is the
                      // only thing saying the row is late.
                      <CalendarClock aria-label="Overdue" role="img"
                        className="h-3 w-3 shrink-0 text-danger-fg" />
                    )}
                    {formatDate(line.order_date)}
                  </span>
                </Td>
                <Td className="font-mono text-xs">{line.material_code}</Td>
                <Td className="text-xs">
                  {line.partner_code ?? (
                    <span title="No supplier on record for this material — set one in Supply Parameters"
                      className="text-danger-fg">none</span>
                  )}
                  {line.lead_time_missing && (
                    <span title="No lead time on record — the order date assumes the goods arrive instantly"
                      className="ml-1 text-danger-fg">?</span>
                  )}
                </Td>
                <Td align="right" className="font-mono text-xs text-fg-muted">{formatDate(line.need_week)}</Td>
                <Td align="right" className="font-mono text-fg-secondary">{fmt(line.gross_qty)}</Td>
                <Td align="right" className="font-mono text-fg-secondary">{fmt(line.available_qty)}</Td>
                <Td align="right" className="font-mono">{fmt(line.net_qty)}</Td>
                <Td align="right" className="font-mono font-semibold">
                  <span title={Number(line.raised_to_moq) > 0
                    ? `Includes ${fmt(line.raised_to_moq)} added to reach the minimum order quantity`
                    : undefined}>
                    {fmt(line.suggested_qty)}
                    {Number(line.raised_to_moq) > 0 && <span className="ml-1 text-warning-fg">▲</span>}
                  </span>
                </Td>
                <Td align="center">
                  {canExecute ? (
                    <Select selectSize="sm"
                      value={line.status}
                      disabled={setStatus.isPending}
                      onChange={(e) => setStatus.mutate({
                        line, status: e.target.value as PurchaseLineStatus,
                      })}
                     
                    >
                      <option value="pending">Pending</option>
                      <option value="ordered">Ordered</option>
                      <option value="ignored">Ignored</option>
                    </Select>
                  ) : (
                    <Badge variant={STATUS_VARIANT[line.status]}>{line.status}</Badge>
                  )}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>

      {run && lines.length > 0 && (
        // A legend, not just tooltips: a tooltip only helps somebody who
        // already suspects the mark means something, and a red icon on a
        // purchase list is exactly the kind of thing people learn to ignore
        // rather than hover over.
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
          <span className="flex items-center gap-1">
            <CalendarClock aria-hidden className="h-3 w-3 text-danger-fg" />
            Order date has passed — ordering now arrives late
          </span>
          <span className="flex items-center gap-1">
            <span className="text-warning-fg">▲</span>
            Quantity raised to reach the supplier&apos;s minimum order
          </span>
          <span className="flex items-center gap-1">
            <span className="text-danger-fg">none / ?</span>
            No supplier / no lead time on file — set them in Supply Parameters
          </span>
        </div>
      )}

      {run && Number(run.raw_material_loss_rate) === 0 && Number(run.packaging_loss_rate) === 0 && (
        <p className="flex items-start gap-1.5 text-xs text-fg-muted">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Both loss rates were 0 for this calculation, so quantities equal the BOM exactly.
          In-transit stock is not counted yet either — these are requirements, not a net
          position against what is already on order.
        </p>
      )}
    </div>
  )
}
