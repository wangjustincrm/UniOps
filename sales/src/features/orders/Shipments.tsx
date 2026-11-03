import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, Badge, CopyChip, DateCell, Tooltip, ErrorMessage, LocalTable, StatusCell, queryKeys, type ColumnDef } from '@/platform-ui'
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
// Where the ship date came from, and the 945's W27 mode, in words (the codes in a tooltip).
const DATE_SOURCE: Record<string, string> = { G62: "From the 945's G62*11 (ship date)", DTM: "From the 945's DTM*011 (shipped)" }
const MODE: Record<string, string> = { U: 'Parcel', LT: 'LTL', M: 'Truck' }

function Pair({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="min-w-0 break-words text-neutral-800">{children}</dd>
    </div>
  )
}

export function Shipments({ shipments, orderId }: { shipments: ShipmentRead[]; orderId: number }) {
  if (shipments.length === 0) return null
  return (
    <section aria-label="Shipments" className="flex flex-col gap-3">
      {shipments.map((s) => (
        <div key={s.id} aria-label={`Shipment ${s.id}`} role="group" className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-neutral-800">945 #{s.document_id}</span>
            {s.shadow && <Badge variant="neutral">Shadow run: not applied</Badge>}
            {!s.shadow && !s.applied && <Badge variant="warning">Not applied (second 945)</Badge>}
            <span className="ml-auto"><PackingListButton s={s} orderId={orderId} /></span>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            <Pair label="Ship date">
              {s.ship_date ? <Tooltip tip={DATE_SOURCE[s.ship_date_source ?? ''] ?? 'From the 945'}><DateCell value={s.ship_date} /></Tooltip> : '—'}
            </Pair>
            <Pair label="Carrier">
              {s.carrier ? <>{s.carrier}{s.mode && <> · <Tooltip tip={`W27 mode ${s.mode}`}>{MODE[s.mode] ?? s.mode}</Tooltip></>}</> : '—'}
            </Pair>
            <Pair label="Tracking">
              {s.tracking?.length ? <span className="flex flex-wrap gap-1">{s.tracking.map((t) => <CopyChip key={t} text={t} />)}</span> : 'None'}
            </Pair>
            <Pair label="Packed">{s.rows.length} {s.rows.length === 1 ? 'row' : 'rows'} · {s.rows.reduce((n, r) => n + r.qty_shipped_ea, 0)} EA</Pair>
            <Pair label="BOL">{s.bol ?? '—'}</Pair>
            <Pair label="940 ref">{s.our_reference ?? '—'}</Pair>
            <Pair label="Shopify">
              <span className="inline-flex flex-wrap items-center gap-1">
                <StatusCell value={s.shopify_status} />
                {(s.shopify_status === 'Failed' || s.shopify_status === 'Suppressed') && !s.shadow && <Retry s={s} orderId={orderId} />}
              </span>
            </Pair>
          </dl>
          {s.shopify_error && <p className="text-xs text-danger-600">{s.shopify_error}</p>}
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
