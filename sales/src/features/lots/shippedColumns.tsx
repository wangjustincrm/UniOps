import { Badge, type ColumnDef, columnsOf } from '@/platform-ui'
import type { ShippedRead } from './lotsApi'

const col = columnsOf<ShippedRead>()

/** Shipped (the applied 945 rows): who got what, when; an item's or a lot's Shipped tab, and the Shipped page. */
export const shippedColumns: ColumnDef<ShippedRead, unknown>[] = [
  col.date('ship_date', 'Ship date'),
  col.text('customer', 'Customer'),
  col.text('order_ref', 'Order ref'),
  col.text('item_code', 'Item'),
  col.text('lot', 'Lot'),
  col.date('expiry', 'Expiry'),
  col.number('qty_shipped_ea', 'Qty EA'),
  { accessorKey: 'delivery_confirmed', header: 'Delivery', cell: ({ row }) => (row.original.delivery_confirmed
    ? <Badge variant="success">Confirmed</Badge> : <Badge variant="neutral">Not confirmed</Badge>) },
  col.tracking('tracking', 'Tracking', { url: 'tracking_url' }),
  col.text('ship_to_city', 'Ship-to city'),
]
