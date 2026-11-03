import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, Badge, CopyChip, DateCell, ErrorMessage, LocalTable, StatusCell, queryKeys, type ColumnDef } from '@/platform-ui'
import { downloadPackingList, ordersClient, retryShopify, type ShipmentRead, type ShipmentRowRead } from './ordersApi'

const errorMessages: Record<string, string> = {
  'shipment.shadow': 'The shadow run is on (or this 945 came during it): nothing is written to Shopify.',
  'shipment.not_failed': 'The Shopify write is not failed.',
  'auth.forbidden': 'You do not have permission to retry Shopify writes.',
}

const rowColumns: ColumnDef<ShipmentRowRead, unknown>[] = [
  { id: 'pallet', header: 'Pallet (SSCC)', cell: ({ row }) => row.original.sscc ?? `LX ${row.original.lx_index}` },
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? '—' },
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ?? '—' },
  { accessorKey: 'expiry', header: 'Expiry', cell: ({ row }) => <DateCell value={row.original.expiry} /> },
  { id: 'qty', header: 'Shipped / ordered EA', cell: ({ row }) => `${row.original.qty_shipped_ea} / ${row.original.qty_ordered_ea ?? '—'}` },
  { id: 'review', header: '', cell: ({ row }) => row.original.needs_review && <Badge variant="danger">matches no order line</Badge> },
]

function PackingListButton({ s, orderId }: { s: ShipmentRead; orderId: number }) {
  const [error, setError] = useState<unknown>(null)
  return (
    <span className="inline-flex flex-col gap-1">
      <ActionButton size="sm" variant="secondary" onAction={() => downloadPackingList(orderId, s.id).catch(setError)}>
        Packing list (PDF)
      </ActionButton>
      <ErrorMessage error={error} />
    </span>
  )
}

function Retry({ s, orderId }: { s: ShipmentRead; orderId: number }) {
  const qc = useQueryClient()
  const m = useMutation({
    mutationFn: () => retryShopify(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, orderId) }),
  })
  return (
    <span className="inline-flex flex-col gap-1">
      <ActionButton size="sm" variant="secondary" permission="sales.order.operate" onAction={() => m.mutateAsync().catch(() => {})}>
        Retry in Shopify
      </ActionButton>
      <ErrorMessage error={m.error} messages={errorMessages} />
    </span>
  )
}

/** The order's 945s: date, carrier, tracking, the checks that notified
 *  (FR-OB-25d) and the Shopify write-back (FR-OB-11). The pallet rows with
 *  their lots (FR-OB-25b) are under each line in Lines; a row matching no
 *  line is listed here. */
export function Shipments({ shipments, orderId }: { shipments: ShipmentRead[]; orderId: number }) {
  if (shipments.length === 0) return null
  return (
    <section aria-label="Shipments" className="flex flex-col gap-3">
      {shipments.map((s) => (
        <div key={s.id} aria-label={`Shipment ${s.id}`} role="group" className="flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span>Shipped <DateCell value={s.ship_date} /> ({s.ship_date_source ?? 'no date'})</span>
            {s.carrier && <span>· {s.carrier} {s.mode ?? ''}</span>}
            {s.tracking?.length ? <span className="inline-flex flex-wrap items-center gap-1">· tracking {s.tracking.map((t) => <CopyChip key={t} text={t} />)}</span>
              : <span>· no tracking</span>}
            {s.shadow && <Badge variant="neutral">Shadow run: not applied</Badge>}
            {!s.shadow && !s.applied && <Badge variant="warning">Not applied (second 945)</Badge>}
            <span>· Shopify <StatusCell value={s.shopify_status} /></span>
            {(s.shopify_status === 'Failed' || s.shopify_status === 'Suppressed') && !s.shadow && <Retry s={s} orderId={orderId} />}
            <span className="ml-auto"><PackingListButton s={s} orderId={orderId} /></span>
          </div>
          {s.shopify_error && <p className="text-danger-600">{s.shopify_error}</p>}
          <span>{s.rows.length} {s.rows.length === 1 ? 'row' : 'rows'} · {s.rows.reduce((n, r) => n + r.qty_shipped_ea, 0)} EA
            {s.bol && <> · BOL {s.bol}</>}{s.our_reference && <> · 940 ref. {s.our_reference}</>} (by line in Lines ▸)</span>
          {s.rows.some((r) => r.needs_review) && (
            <LocalTable<ShipmentRowRead> ariaLabel={`Shipment ${s.id} rows matching no line`} columns={rowColumns}
              rows={s.rows.filter((r) => r.needs_review)} rowKey={(r) => `${r.lx_index}-${r.w12_index}`} />
          )}
          {s.checks && s.checks.length > 0 && (
            <ul aria-label="945 checks" className="text-warning-700">
              {s.checks.map((c, i) => <li key={i}>{c.check}: {c.message}</li>)}
            </ul>
          )}
        </div>
      ))}
    </section>
  )
}
