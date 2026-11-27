import { Badge, type ColumnDef, columnsOf, Text, Cluster } from '@/platform-ui'
import { PartnerCell } from '@/features/common/PartnerCell'
import type { LotRead } from './lotsApi'

const colLotRead = columnsOf<LotRead>()

/** A 945 row's columns: the Lots list, and a lot's own page (its Shipped tab). */
export const lotColumns: ColumnDef<LotRead, unknown>[] = [
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => <Text weight="medium">{row.original.lot ?? '—'}</Text> },
  colLotRead.text('item_code', 'Item'),
  colLotRead.date('expiry', 'Expiry'),
  colLotRead.text('qty_shipped_ea', 'Qty EA'),
  colLotRead.date('ship_date', 'Ship date'),
  colLotRead.text('order_ref', 'Order ref'),
  colLotRead.text('order_number', 'Internal no.'),
  { accessorKey: 'partner_name', header: 'Partner', cell: ({ row }) => <PartnerCell name={row.original.partner_name} code={row.original.partner_code} /> },
  { accessorKey: 'ship_to_city', header: 'Ship to', cell: ({ row }) => [row.original.ship_to_city, row.original.ship_to_postal].filter(Boolean).join(' · ') },
  colLotRead.tracking('tracking', 'Tracking', { url: 'tracking_url' }),
  { accessorKey: 'sscc', header: 'Pallet (SSCC)', cell: ({ row }) => row.original.sscc ?? '—' },
  { accessorKey: 'document_id', header: '945', cell: ({ row: { original: r } }) => (
    <Cluster as="span" inline gap={1} align="center">#{r.document_id}
      {!r.shadow && !r.applied && <Badge variant="warning">Second 945</Badge>}
    </Cluster>) },
]

