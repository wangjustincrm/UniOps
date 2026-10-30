import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { QueryTable, Sheet, defaultSpec, type ColumnDef, type QuerySpec } from '@/platform-ui'
import { OrderDetailView, StateBadge } from './OrderDetailView'
import { FULFILLMENT_STATUSES, SOURCE_TYPES, ordersClient, type OrderRead } from './ordersApi'

const orderColumns: ColumnDef<OrderRead, unknown>[] = [
  { accessorKey: 'order_number', header: 'Order' },
  { accessorKey: 'partner_code', header: 'Partner' },
  { accessorKey: 'source_order_number', header: 'Source no.' },
  { accessorKey: 'order_date', header: 'Ordered' },
  { accessorKey: 'fulfillment_status', header: 'Status', cell: ({ row }) => <StateBadge o={row.original} /> },
  { accessorKey: 'total', header: 'Total', cell: ({ row }) => (row.original.total ? `${row.original.total} ${row.original.currency}` : '') },
]

/** A list of orders on the shared query contract, with the order's detail in a sheet. */
export function OrderList({ fixed = [], title, description, emptyTitle, detailExtra }: {
  fixed?: QuerySpec['filters']; title: string; description: string; emptyTitle: string
  detailExtra?: (o: OrderRead, close: () => void) => React.ReactNode
}) {
  const [spec, setSpec] = useState<QuerySpec>({ ...defaultSpec(), filters: fixed ?? [] })
  const [open, setOpen] = useState<OrderRead | null>(null)
  const list = useQuery({ queryKey: ['orders', spec], queryFn: () => ordersClient.list(spec), placeholderData: keepPreviousData })
  const filters = fixed?.length ? [] : [
    { field: 'fulfillment_status', label: 'Status', options: FULFILLMENT_STATUSES.map((v) => ({ value: v, label: v })) },
    { field: 'source_type', label: 'Source', options: SOURCE_TYPES.map((v) => ({ value: v, label: v })) },
  ]
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">{title}</h1>
        <p className="text-sm text-neutral-500">{description}</p>
      </div>
      <QueryTable<OrderRead>
        columns={orderColumns} rows={list.data?.items ?? []} total={list.data?.total ?? 0} spec={spec}
        onSpecChange={(s) => setSpec({ ...s, filters: [...(fixed ?? []), ...(s.filters ?? []).filter((f) => !fixed?.some((x) => x.field === f.field))] })}
        sortable={['order_number', 'first_received_at', 'order_date', 'partner_code', 'fulfillment_status']}
        searchable filters={filters} loading={list.isLoading} error={list.error} onRetry={() => list.refetch()}
        rowKey={(o) => o.id} onRowClick={setOpen} emptyTitle={emptyTitle}
      />
      <Sheet open={open !== null} onOpenChange={(o) => { if (!o) setOpen(null) }} title={open ? `Order ${open.order_number}` : ''} className="max-w-3xl">
        {open && (
          <div className="flex flex-col gap-4">
            {detailExtra?.(open, () => setOpen(null))}
            <OrderDetailView id={open.id} />
          </div>
        )}
      </Sheet>
    </div>
  )
}

/** Orders: every order from Shopify, retailer 850s and manual entry. */
export default function OrdersPage() {
  return <OrderList title="Orders" description="Orders from the Shopify store, retailer 850s and manual entry. Historical orders are left out unless asked for." emptyTitle="No orders yet" />
}
