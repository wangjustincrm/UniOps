import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { QueryTable, Sheet, useListView, type ColumnDef, type QuerySpec } from '@/platform-ui'
import { OrderDetailView, StateBadge } from './OrderDetailView'
import { Recheck } from './Recheck'
import { ordersClient, type OrderRead } from './ordersApi'

const orderColumns: ColumnDef<OrderRead, unknown>[] = [
  { accessorKey: 'order_number', header: 'Order' },
  { accessorKey: 'partner_code', header: 'Partner' },
  { accessorKey: 'source_order_number', header: 'Source no.' },
  { accessorKey: 'order_date', header: 'Ordered' },
  { accessorKey: 'fulfillment_status', header: 'Status', cell: ({ row }) => <StateBadge o={row.original} /> },
  { accessorKey: 'total', header: 'Total', cell: ({ row }) => (row.original.total ? `${row.original.total} ${row.original.currency}` : '') },
  { accessorKey: 'review_reason', header: 'Review reason', cell: ({ row }) => (
    <span className="line-clamp-2 max-w-md whitespace-pre-line text-xs text-neutral-600">{row.original.review_reason ?? ''}</span>
  ) },
  // Review orders: Re-check right in the list (FR-OB-15).
  { id: 'actions', header: '', cell: ({ row }) => (row.original.fulfillment_status === 'Review' ? <Recheck o={row.original} compact /> : null) },
]

/** A list of orders on the shared query contract, with the order's detail in
 *  a sheet. With `fixed` filters (a list inside another page) it has no views. */
export function OrderList({ fixed = [], title, description, emptyTitle, detailExtra }: {
  fixed?: QuerySpec['filters']; title: string; description: string; emptyTitle: string
  detailExtra?: (o: OrderRead, close: () => void) => React.ReactNode
}) {
  const view = useListView(ordersClient, { fixed })
  const { spec } = view
  const [open, setOpen] = useState<OrderRead | null>(null)
  const list = useQuery({ queryKey: ['orders', spec], queryFn: () => ordersClient.list(spec), placeholderData: keepPreviousData, enabled: view.ready })
  // The open order as the list now has it (a re-check may have moved it on).
  const current = open ? (list.data?.items.find((o) => o.id === open.id) ?? open) : null
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">{title}</h1>
        <p className="text-sm text-neutral-500">{description}</p>
      </div>
      <QueryTable<OrderRead>
        columns={orderColumns} rows={list.data?.items ?? []} total={list.data?.total ?? 0} {...view.tableProps}
        groups={list.data?.groups} groupsTruncated={list.data?.groups_truncated}
        resource={ordersClient} locked={(fixed ?? []).map((f) => f.field)}
        sortable={['order_number', 'first_received_at', 'order_date', 'partner_code', 'fulfillment_status']}
        searchable loading={list.isLoading} error={list.error} onRetry={() => list.refetch()}
        rowKey={(o) => o.id} onRowClick={setOpen} emptyTitle={emptyTitle}
      />
      <Sheet open={current !== null} onOpenChange={(o) => { if (!o) setOpen(null) }} title={current ? `Order ${current.order_number}` : ''} className="max-w-3xl">
        {current && (
          <div className="flex flex-col gap-4">
            {current.fulfillment_status === 'Review' && <Recheck o={current} onResolved={() => setOpen(null)} />}
            {detailExtra?.(current, () => setOpen(null))}
            <OrderDetailView id={current.id} />
          </div>
        )}
      </Sheet>
    </div>
  )
}

/** Orders: every order from Shopify, retailer 850s and manual entry. Its
 *  system views include Needs review (the old Review worklist). */
export default function OrdersPage() {
  return <OrderList title="Orders" description="Orders from the Shopify store, retailer 850s and manual entry. Choose a view (e.g. Needs review), or search, sort and group the list yourself." emptyTitle="No orders here" />
}
