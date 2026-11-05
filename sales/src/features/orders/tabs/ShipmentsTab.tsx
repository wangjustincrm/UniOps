import { Badge, CopyChip, DateCell, LocalTable, Section, TrackingLink, type ColumnDef } from '@/platform-ui'
import { lineNumbers, type OrderDetail, type PackingRead } from '../ordersApi'
import { Shipments } from '../Shipments'

// Every pallet row the warehouse packed for the order, with its lot and
// tracking (a recall's view of this order).
const lotColumns: ColumnDef<PackingRead & { item: string | null; links: Map<string, string | null> }, unknown>[] = [
  { accessorKey: 'item', header: 'Item' },
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ? <CopyChip text={row.original.lot} /> : '—' },
  { accessorKey: 'expiry', header: 'Expiry', cell: ({ row }) => <DateCell value={row.original.expiry} /> },
  { accessorKey: 'qty_shipped_ea', header: 'Qty EA', cell: ({ row }) => <span className="tabular-nums">{row.original.qty_shipped_ea}</span> },
  { accessorKey: 'sscc', header: 'Pallet (SSCC)', cell: ({ row }) => row.original.sscc ?? '—' },
  { accessorKey: 'tracking', header: 'Tracking', cell: ({ row: { original: p } }) => p.tracking
    ? <span className="flex flex-wrap gap-1">{p.tracking.split(', ').map((t) => <TrackingLink key={t} number={t} url={p.links.get(t)} />)}</span> : '—' },
  { accessorKey: 'ship_date', header: 'Ship date', cell: ({ row }) => <DateCell value={row.original.ship_date} /> },
  { id: 'doc', header: '945', cell: ({ row: { original: p } }) => (
    <span className="inline-flex items-center gap-1">#{p.document_id}
      {!p.shadow && !p.applied && <Badge variant="warning">Second 945</Badge>}
    </span>) },
]

function Lots({ o }: { o: OrderDetail }) {
  // the carrier's link per tracking number, from the shipment it came on
  const links = new Map(o.shipments.map((s) => [s.id, new Map((s.tracking_links ?? []).map((t) => [t.number, t.url] as const))]))
  const none = new Map<string, string | null>()
  const rows = o.lines.flatMap((ln) => (ln.packing ?? []).map((p) => ({ ...p, item: ln.item_code, links: links.get(p.shipment_id) ?? none })))
  if (rows.length === 0) return null
  return (
    <Section title="Pallets and lots">
      <LocalTable ariaLabel="Pallets and lots" columns={lotColumns} rows={rows} rowKey={(p, i) => `${p.shipment_id}-${i}`}
        sortable={['item', 'lot', 'expiry', 'ship_date']}
        footer={[null, null, 'Total', rows.reduce((n, p) => n + p.qty_shipped_ea, 0), null, null, null, null]} />
    </Section>
  )
}

/** The order's 945s, then every pallet and lot packed for it. */
export function ShipmentsTab({ o }: { o: OrderDetail }) {
  return (
    <>
      <Shipments shipments={o.shipments} orderId={o.id} lineNumbers={lineNumbers(o)} lines={o.lines} />
      <Lots o={o} />
    </>
  )
}
