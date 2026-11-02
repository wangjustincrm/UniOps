import { useQuery } from '@tanstack/react-query'
import {
  AddressView, Badge, DateTimeCell, DetailList, ErrorView, LocalTable, Skeleton, StatusCell, formatDate, formatDateTime, formatMoney, queryKeys,
  type ColumnDef,
} from '@/platform-ui'
import { fetchOrder, ordersClient, type LineRead, type OrderDetail, type OrderDocument, type OrderRead } from './ordersApi'
import { Shipments } from './Shipments'

// The badge shows the fulfillment status in the colour of the derived order state (D17).
const stateTone = { Draft: 'neutral', Active: 'info', 'On Hold': 'warning', Completed: 'success', Cancelled: 'neutral' } as const

export function StateBadge({ o }: { o: Pick<OrderRead, 'order_state' | 'fulfillment_status'> }) {
  return <StatusCell value={o.fulfillment_status} tone={stateTone[o.order_state]} />
}

// Where each address came from, under it (the 850 ship-to also names its GLN and the DC it routes to).
function shipToOrigin(o: OrderDetail): string {
  if (o.source_type === 'SHOPIFY') return 'From the Shopify shipping address'
  if (o.source_type === 'MANUAL') return 'Entered by hand'
  const gln = o.ship_to?.gln
  return ['From the 850 ShipTo', gln && `GLN ${gln}`, o.dc_number && `DC ${o.dc_number}`].filter(Boolean).join(' · ')
}
function billToOrigin(o: OrderDetail): string {
  if (o.source_type === 'SHOPIFY') return 'From the Shopify billing address (the shipping address when missing or outside Canada)'
  if (o.source_type === 'MANUAL') return 'None for a manual order'
  return ['From the 850 BillTo', o.bill_to?.code && `code ${o.bill_to.code}`].filter(Boolean).join(' · ')
}

const lineColumns: ColumnDef<LineRead, unknown>[] = [
  { accessorKey: 'line_no', header: '#' },
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? <span className="text-danger-600">unmatched</span> },
  { id: 'ordered', header: 'Ordered', cell: ({ row }) => `${Number(row.original.ordered_qty)} ${row.original.order_unit ?? '?'}` },
  { accessorKey: 'qty_ea', header: 'EA', cell: ({ row }) => row.original.qty_ea ?? '—' },
  { accessorKey: 'problem', header: 'Note', cell: ({ row }) => (
    <span className={row.original.problem && !row.original.removed ? 'text-danger-600' : ''}>{row.original.problem ?? ''}</span>
  ) },
]

// File and 997 status apart: Sent is not Accepted (FR-OB-22); no 997 column until the file is sent.
const sentColumns: ColumnDef<OrderDocument, unknown>[] = [
  { accessorKey: 'st02', header: 'Control' },
  { accessorKey: 'file_status', header: 'File', cell: ({ row }) => <StatusCell value={row.original.file_status} /> },
  { accessorKey: 'ack_status', header: '997', cell: ({ row: { original: d } }) => (
    d.file_status === 'Sent' || d.ack_status !== 'Waiting' ? <StatusCell value={d.ack_status} /> : '—') },
  { accessorKey: 'sent_at', header: 'Sent', cell: ({ row }) => <DateTimeCell value={row.original.sent_at} /> },
  { id: 'note', header: 'Note', cell: ({ row }) => <span className="break-words">{row.original.error ?? row.original.ack_detail ?? ''}</span> },
]

/** One order: why it is held, its lines in EA, its source documents and what happened (FR-OB-53). */
export function OrderDetailView({ id }: { id: number }) {
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, id), queryFn: () => fetchOrder(id) })
  if (q.isLoading) return <Skeleton className="h-40 w-full" />
  if (q.error || !q.data) return <ErrorView error={q.error} />
  const o = q.data
  const sent = o.documents.filter((d) => d.direction === 'OUT' && d.kind === '940_EDI')
  const sources = o.documents.filter((d) => d.direction === 'IN')
  return (
    <div className="flex flex-col gap-5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StateBadge o={o} />
        <span className="text-neutral-500">{o.order_state}</span>
        {o.historical && <Badge variant="neutral">Historical</Badge>}
        {o.archive_suppressed && <Badge variant="warning">Archived in Shopify</Badge>}
      </div>
      {o.review_reason && (
        <section aria-label="Review reason" className="rounded-md border border-warning-200 bg-warning-50 p-2">
          <p className="font-medium text-warning-700">Needs review</p>
          <pre className="whitespace-pre-wrap text-xs text-warning-700">{o.review_reason}</pre>
        </section>
      )}
      <DetailList items={[
        ['Order', `${o.order_number} · ${o.partner_code}`], ['Customer PO / reference', o.source_order_number], ['Reference', o.source_reference],
        ['Source', o.source_type], ['Received', formatDateTime(o.first_received_at)], ['Paid (delay start)', formatDateTime(o.paid_at)],
        ['Payment', o.shopify_financial_status], ['Requested delivery', o.requested_delivery_date ? formatDate(o.requested_delivery_date) : null],
        ['Ship to', <span key="st" className="flex flex-col gap-1"><AddressView value={o.ship_to} note={shipToOrigin(o)} />
          {(o.ship_to_flags ?? []).map((f) => <Badge key={f} variant="warning">{f}</Badge>)}</span>],
        ['Bill to', <AddressView key="bt" value={o.bill_to} note={billToOrigin(o)} />],
        ['Total', formatMoney(o.total, o.currency)], ['Invoice', o.invoice_status],
      ]} />
      <section aria-label="Lines">
        <h3 className="mb-1 font-semibold text-neutral-800">Lines</h3>
        <LocalTable<LineRead> ariaLabel="Order lines" columns={lineColumns} rows={o.lines} rowKey={(ln) => ln.id}
          rowAttrs={(ln) => (ln.removed ? { className: 'text-neutral-400', 'data-removed': true } : {})} />
      </section>
      <section aria-label="Documents">
        <h3 className="mb-1 font-semibold text-neutral-800">Source documents</h3>
        {sources.length === 0 ? <p className="text-neutral-500">None (entered by hand).</p> : (
          <ul className="text-xs">{sources.map((d) => <li key={d.id}>#{d.id} {d.kind} · {d.remote_name} · {formatDateTime(d.created_at)}</li>)}</ul>
        )}
      </section>
      {sent.length > 0 && (
        <section aria-label="Warehouse 940s">
          <h3 className="mb-1 font-semibold text-neutral-800">Warehouse 940s</h3>
          <LocalTable<OrderDocument> ariaLabel="Warehouse 940s" columns={sentColumns} rows={sent} rowKey={(d) => d.id} />
        </section>
      )}
      <Shipments shipments={o.shipments ?? []} orderId={o.id} />
      <section aria-label="Timeline">
        <h3 className="mb-1 font-semibold text-neutral-800">Timeline</h3>
        <ol className="flex flex-col gap-1 text-xs">
          {o.timeline.map((e, i) => (
            <li key={i}>{formatDateTime(e.at)} · {e.user_name} · {e.reason ?? e.action}
              {e.after && 'fulfillment_status' in e.after && <> · {String(e.before?.fulfillment_status ?? '—')} → {String(e.after.fulfillment_status)}</>}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
