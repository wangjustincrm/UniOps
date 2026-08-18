// Inventory → Aging. Shelf life at 180 / 60 / 30 days, and what is already gone.
//
// Five cards over a table. Clicking a card filters the table to that band, so
// "92 lots expired" is one click from "which 92".
//
// Expired and under-30 are selected on arrival, because those are the two bands
// that require somebody to do something today.
//
// ★ The cards count SUPPLIER BATCHES, not WMS lots, because that is what the
// table below them lists. 3,532 lots are only 877 batches and one batch can
// hold 192 of them, so a card counting lots over a table of batches would be a
// discrepancy nobody could explain and nothing would report. The lot count is
// still shown, underneath, as the secondary figure it is.
import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AlertTriangle, ChevronLeft, ChevronRight, Info } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  AGING_BUCKET_ORDER, formatDateOnly, inventoryApi, qty,
  type AgingBucketKey,
} from './inventoryApi'

const PAGE_SIZE = 25

const CARD_STYLE: Record<AgingBucketKey, { idle: string; active: string }> = {
  expired: {
    idle: 'border-danger-200 bg-danger-50/60 hover:bg-danger-50',
    active: 'border-danger-500 bg-danger-50 ring-1 ring-danger-500',
  },
  under_30: {
    idle: 'border-warning-200 bg-warning-50/60 hover:bg-warning-50',
    active: 'border-warning-500 bg-warning-50 ring-1 ring-warning-500',
  },
  '30_to_60': {
    idle: 'border-neutral-200 bg-white hover:bg-neutral-50',
    active: 'border-primary-500 bg-primary-50 ring-1 ring-primary-500',
  },
  '60_to_180': {
    idle: 'border-neutral-200 bg-white hover:bg-neutral-50',
    active: 'border-primary-500 bg-primary-50 ring-1 ring-primary-500',
  },
  over_180: {
    idle: 'border-neutral-200 bg-white hover:bg-neutral-50',
    active: 'border-primary-500 bg-primary-50 ring-1 ring-primary-500',
  },
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function AgingTab({ erpClassCode }: { erpClassCode: string }) {
  const [selected, setSelected] = useState<AgingBucketKey>('expired')
  const [page, setPage] = useState(1)

  useEffect(() => { setPage(1) }, [selected, erpClassCode])

  const agingQuery = useQuery({
    queryKey: ['inventory-aging', erpClassCode],
    queryFn: () => inventoryApi.aging({ erp_class_code: erpClassCode || undefined }),
  })

  const asOf = agingQuery.data?.as_of

  // The band is sent as a NAME, not as a date window rebuilt here. The server
  // resolves it from the same thresholds it counted with, so the number on a
  // card and the rows under it are two views of one definition rather than two
  // implementations of it.
  const lotFilters = useMemo(() => ({
    erp_class_code: erpClassCode || undefined,
    aging_bucket: selected,
    sort: 'expiry_date' as const,
  }), [erpClassCode, selected])

  const lotsQuery = useQuery({
    queryKey: ['inventory-aging-batches', page, lotFilters],
    queryFn: () => inventoryApi.listBatches(page, PAGE_SIZE, lotFilters),
    placeholderData: keepPreviousData,
  })

  const buckets = agingQuery.data?.buckets ?? []
  const byKey = new Map(buckets.map((b) => [b.key, b]))
  const items = lotsQuery.data?.items ?? []
  const total = lotsQuery.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="flex flex-col gap-3">
      {agingQuery.isError && (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {errMsg(agingQuery.error, 'Could not load the shelf-life summary.')}
        </p>
      )}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {AGING_BUCKET_ORDER.map((key) => {
          const bucket = byKey.get(key)
          const active = selected === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => setSelected(key)}
              aria-pressed={active}
              className={cn(
                'rounded-lg border p-3 text-left transition-colors',
                active ? CARD_STYLE[key].active : CARD_STYLE[key].idle,
              )}
            >
              <div className="text-[11px] font-medium uppercase tracking-wide text-neutral-500">
                {bucket?.label ?? key}
              </div>
              <div className="mt-1 text-xl font-semibold tabular-nums text-neutral-900">
                {agingQuery.isLoading ? '—' : (bucket?.batches ?? 0).toLocaleString('en-US')}
                <span className="ml-1 text-xs font-normal text-neutral-500">batches</span>
              </div>
              <div className="text-xs tabular-nums text-neutral-500">
                {agingQuery.isLoading ? '' : qty(bucket?.qty ?? '0')}
                {!agingQuery.isLoading && (
                  <span className="text-neutral-400">
                    {' · '}{(bucket?.lots ?? 0).toLocaleString('en-US')} lots
                  </span>
                )}
              </div>
            </button>
          )
        })}
      </div>

      {/* Excluded, and how many — never silently dropped. Without the reason,
          "1,640 lots not shown" reads as a bug. */}
      {agingQuery.data && agingQuery.data.no_expiry_lots > 0 && (
        <p className="flex items-start gap-1.5 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-neutral-400" />
          <span>
            {agingQuery.data.no_expiry_batches.toLocaleString('en-US')} batch(es)
            ({agingQuery.data.no_expiry_lots.toLocaleString('en-US')} lots,
            {' '}{qty(agingQuery.data.no_expiry_qty)}) have no expiry date and are not
            in any band — packaging and hardware do not expire.
          </span>
        </p>
      )}

      {lotsQuery.isError && (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {errMsg(lotsQuery.error, 'Could not load the stock in this band.')}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-3 py-2 text-left">Material</th>
              <th className="px-3 py-2 text-left">Supplier batch</th>
              <th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Lots</th>
              <th className="px-3 py-2 text-left">Produced</th>
              <th className="px-3 py-2 text-left">Expiry</th>
              <th className="px-3 py-2 text-right">Days</th>
              <th className="px-3 py-2 text-left">Quality</th>
            </tr>
          </thead>
          <tbody>
            {lotsQuery.isLoading && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-neutral-400">Loading stock…</td></tr>
            )}
            {!lotsQuery.isLoading && items.length === 0 && !lotsQuery.isError && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-sm text-neutral-400">
                Nothing in this band.
              </td></tr>
            )}
            {items.map((lot) => (
              <tr
                key={`${lot.warehouse_id}|${lot.material_code}|${lot.supplier_batch ?? ''}`}
                className="border-t border-neutral-100"
              >
                <td className="px-3 py-2">
                  <span className="font-mono text-xs">{lot.material_code}</span>
                  {lot.material_name && (
                    <span className="ml-1.5 text-xs text-neutral-500">{lot.material_name}</span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs">
                  {lot.supplier_batch ?? (
                    <span className="font-sans text-neutral-400">no supplier batch</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {qty(lot.qty)}
                  {lot.base_uom && (
                    <span className="ml-1 font-sans text-xs text-neutral-400">{lot.base_uom}</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-neutral-500">{lot.lots}</td>
                <td className="px-3 py-2 text-xs text-neutral-600">
                  {formatDateOnly(lot.production_date)}
                </td>
                <td className="px-3 py-2 text-xs text-neutral-600">
                  {formatDateOnly(lot.expiry_date)}
                  {lot.expiry_spans_dates && (
                    <span
                      className="ml-1 cursor-help text-neutral-400"
                      title="This batch's lots have different expiry dates — the earliest is shown"
                    >+</span>
                  )}
                </td>
                <td className={cn(
                  'px-3 py-2 text-right font-mono text-xs',
                  lot.days_to_expiry !== null && lot.days_to_expiry < 0 && 'text-danger-600',
                  lot.days_to_expiry !== null && lot.days_to_expiry >= 0 && lot.days_to_expiry < 30
                    && 'text-warning-700',
                )}>
                  {lot.days_to_expiry ?? '—'}
                </td>
                <td className="px-3 py-2 text-xs text-neutral-600">
                  {lot.quality_status_label ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-neutral-500">
          {total.toLocaleString('en-US')} batch(es) in this band
          {asOf && <> · as of {formatDateOnly(asOf)}</>}
        </span>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || lotsQuery.isFetching} aria-label="Previous page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-[11px] text-neutral-500">Page {page} of {pageCount}</span>
          <button type="button" onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={page >= pageCount || lotsQuery.isFetching} aria-label="Next page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
