import { DateCell, ListPage, MoneyCell, type ColumnDef, type QuerySpec } from '@/platform-ui'
import { OrderDetailView, StateBadge } from './OrderDetailView'
import { NextSends } from './NextSends'
import { Recheck } from './Recheck'
import { Send940 } from './Send940'
import { ordersClient, type OrderRead } from './ordersApi'

const orderColumns: ColumnDef<OrderRead, unknown>[] = [
  { accessorKey: 'order_number', header: 'Order' },
  { accessorKey: 'partner_code', header: 'Partner' },
  { accessorKey: 'source_order_number', header: 'Source no.' },
  { accessorKey: 'order_date', header: 'Ordered', cell: ({ row }) => <DateCell value={row.original.order_date} /> },
  { accessorKey: 'fulfillment_status', header: 'Status', cell: ({ row }) => <StateBadge o={row.original} /> },
  { accessorKey: 'total', header: 'Total', cell: ({ row }) => <MoneyCell amount={row.original.total} currency={row.original.currency} /> },
  { accessorKey: 'review_reason', header: 'Review reason', cell: ({ row }) => (
    <span className="line-clamp-2 max-w-md whitespace-pre-line text-xs text-neutral-600">{row.original.review_reason ?? ''}</span>
  ) },
]

/** A list of orders with the order's detail in a sheet; Review orders carry
 *  Re-check in the list and the detail (FR-OB-15). With `fixed` filters (a
 *  list inside another page) it has no views. */
export function OrderList({ fixed, title, description, emptyTitle, detailExtra, toolbar }: {
  fixed?: QuerySpec['filters']; title: string; description: string; emptyTitle: string
  detailExtra?: (o: OrderRead, close: () => void) => React.ReactNode
  toolbar?: React.ReactNode
}) {
  return (
    <ListPage<OrderRead>
      resource={ordersClient}
      title={title}
      description={description}
      emptyTitle={emptyTitle}
      fixed={fixed}
      columns={orderColumns}
      toolbar={toolbar ? () => toolbar : undefined}
      rowActions={(o) => (o.fulfillment_status === 'Review' ? <Recheck o={o} compact /> : null)}
      detailTitle={(o) => `Order ${o.order_number}`}
      detailClassName="max-w-3xl"
      renderDetail={(o, close) => (
        <div className="flex flex-col gap-4">
          {o.fulfillment_status === 'Review' && <Recheck o={o} onResolved={close} />}
          <Send940 o={o} />
          {detailExtra?.(o, close)}
          <OrderDetailView id={o.id} />
        </div>
      )}
    />
  )
}

/** Orders: every order from Shopify, retailer 850s and manual entry. Its
 *  system views include Needs review (the old Review worklist). */
export default function OrdersPage() {
  return <OrderList title="Orders" description="Orders from the Shopify store, retailer 850s and manual entry. Choose a view (e.g. Needs review), or search, sort and group the list yourself." emptyTitle="No orders here" toolbar={<NextSends />} />
}
