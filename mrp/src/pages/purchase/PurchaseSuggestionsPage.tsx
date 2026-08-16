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
import { Badge, Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { saveBlob } from '@/pages/forecast/forecastApi'
import { cn } from '@/lib/utils'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import {
  purchaseApi, type PurchaseLine, type PurchaseLineStatus,
} from './purchaseApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
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

  const stats = run?.stats ?? null

  return (
    <div className="flex flex-col gap-4 p-4">
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />

      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-neutral-900">Purchase Suggestions</h1>
          <p className="text-xs text-neutral-500">
            What to buy for the production plan currently in force, and when to order it.
            {run && <> Calculated {new Date(run.created_at).toLocaleString()} · {run.run_no}</>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {runs.length > 1 && (
            <select
              value={activeId ?? ''}
              onChange={(e) => setRunId(e.target.value)}
              className="h-11 rounded-lg border border-neutral-300 px-3 text-sm"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.run_no} · {new Date(r.created_at).toLocaleDateString()}
                </option>
              ))}
            </select>
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
          <span className="rounded-md border border-neutral-200 bg-white px-2 py-1">
            {stats.components} component(s) · {stats.lines} line(s)
          </span>
          {stats.products_without_bom > 0 && (
            <span className="rounded-md border border-warning-200 bg-warning-50 px-2 py-1 text-warning-800">
              {stats.products_without_bom} product(s) have no BOM — nothing was exploded for them
            </span>
          )}
          {stats.supplier_missing > 0 && (
            <span className="rounded-md border border-danger-200 bg-danger-50 px-2 py-1 text-danger-700">
              {stats.supplier_missing} line(s) have no supplier
            </span>
          )}
          {stats.lead_time_missing > 0 && (
            <span className="rounded-md border border-danger-200 bg-danger-50 px-2 py-1 text-danger-700">
              {stats.lead_time_missing} line(s) have no lead time — their order date is a guess
            </span>
          )}
          {stats.order_date_passed > 0 && (
            <span className="rounded-md border border-danger-200 bg-danger-50 px-2 py-1 text-danger-700">
              {stats.order_date_passed} line(s) should already have been ordered
            </span>
          )}
        </div>
      )}

      {run && (
        <label className="flex items-center gap-2 text-xs text-neutral-600">
          <input type="checkbox" checked={hideDone}
            onChange={(e) => setHideDone(e.target.checked)}
            className="h-4 w-4 rounded border-neutral-300" />
          Show only lines still to deal with
        </label>
      )}

      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-3 py-2 text-left">Order by</th>
              <th className="px-3 py-2 text-left">Material</th>
              <th className="px-3 py-2 text-left">Supplier</th>
              <th className="px-3 py-2 text-right">Needed</th>
              <th className="px-3 py-2 text-right">Gross</th>
              <th className="px-3 py-2 text-right">Available</th>
              <th className="px-3 py-2 text-right">Net</th>
              <th className="px-3 py-2 text-right">Suggested</th>
              <th className="px-3 py-2 text-center">Status</th>
            </tr>
          </thead>
          <tbody>
            {runQuery.isLoading && (
              <tr><td colSpan={9} className="px-3 py-6 text-center text-neutral-400">Loading…</td></tr>
            )}
            {!runQuery.isLoading && !run && (
              <tr><td colSpan={9} className="px-3 py-6 text-center text-neutral-400">
                No calculation yet — press Calculate to work out what to buy for the plan in force.
              </td></tr>
            )}
            {run && lines.length === 0 && (
              <tr><td colSpan={9} className="px-3 py-6 text-center text-neutral-400">
                {hideDone ? 'Everything here has been dealt with.' : 'This calculation produced no suggestions.'}
              </td></tr>
            )}
            {lines.map((line) => (
              <tr key={line.id} className={cn('border-t border-neutral-100',
                line.order_date_passed && 'bg-danger-50/40')}>
                <td className="px-3 py-2 font-mono text-xs">
                  <span className="flex items-center gap-1">
                    {line.order_date_passed && (
                      <CalendarClock aria-hidden className="h-3 w-3 shrink-0 text-danger-600" />
                    )}
                    {line.order_date}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-xs">{line.material_code}</td>
                <td className="px-3 py-2 text-xs">
                  {line.partner_code ?? (
                    <span title="No supplier on record for this material — set one in Supply Parameters"
                      className="text-danger-600">none</span>
                  )}
                  {line.lead_time_missing && (
                    <span title="No lead time on record — the order date assumes the goods arrive instantly"
                      className="ml-1 text-danger-600">?</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-neutral-500">{line.need_week}</td>
                <td className="px-3 py-2 text-right font-mono text-neutral-600">{fmt(line.gross_qty)}</td>
                <td className="px-3 py-2 text-right font-mono text-neutral-600">{fmt(line.available_qty)}</td>
                <td className="px-3 py-2 text-right font-mono">{fmt(line.net_qty)}</td>
                <td className="px-3 py-2 text-right font-mono font-bold">
                  <span title={Number(line.raised_to_moq) > 0
                    ? `Includes ${fmt(line.raised_to_moq)} added to reach the minimum order quantity`
                    : undefined}>
                    {fmt(line.suggested_qty)}
                    {Number(line.raised_to_moq) > 0 && <span className="ml-1 text-warning-600">▲</span>}
                  </span>
                </td>
                <td className="px-3 py-2 text-center">
                  {canExecute ? (
                    <select
                      value={line.status}
                      disabled={setStatus.isPending}
                      onChange={(e) => setStatus.mutate({
                        line, status: e.target.value as PurchaseLineStatus,
                      })}
                      className="rounded border border-neutral-300 px-1 py-0.5 text-xs"
                    >
                      <option value="pending">Pending</option>
                      <option value="ordered">Ordered</option>
                      <option value="ignored">Ignored</option>
                    </select>
                  ) : (
                    <Badge variant={STATUS_VARIANT[line.status]}>{line.status}</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {run && Number(run.raw_material_loss_rate) === 0 && Number(run.packaging_loss_rate) === 0 && (
        <p className="flex items-start gap-1.5 text-xs text-neutral-500">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Both loss rates were 0 for this calculation, so quantities equal the BOM exactly.
          In-transit stock is not counted yet either — these are requirements, not a net
          position against what is already on order.
        </p>
      )}
    </div>
  )
}
