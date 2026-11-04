import { DateCell, ListPage, MoneyCell, type ColumnDef, type ListCreate, type QuerySpec, type RecordTab } from '@/platform-ui'
import { OrderSheet } from './OrderDetailView'
import { OrderStatusBadge, ShopifyCell, StatusDetail } from './OrderStatusCells'
import { ReturnAfterShipment } from '@/features/invoices/ReturnAfterShipment'
import { SubmitDraft } from './DraftActions'
import { ManualOrderForm } from './ManualOrderForm'
import { NextSends } from './NextSends'
import { Recheck } from './Recheck'
import { ordersClient, type OrderRead } from './ordersApi'
import { PartnerCell } from '@/features/common/PartnerCell'

const orderColumns: ColumnDef<OrderRead, unknown>[] = [
  { accessorKey: 'source_reference', header: 'Order ref', cell: ({ row }) => <span className="font-medium">{row.original.source_reference ?? ''}</span> },
  { accessorKey: 'order_number', header: 'Internal no.' },
  { accessorKey: 'partner_name', header: 'Partner', cell: ({ row }) => <PartnerCell name={row.original.partner_name} code={row.original.partner_code} /> },
  { accessorKey: 'source_order_number', header: 'Customer PO / ref.' },
  { accessorKey: 'order_date', header: 'Ordered', cell: ({ row }) => <DateCell value={row.original.order_date} /> },
  { accessorKey: 'order_status', header: 'Status', cell: ({ row }) => <OrderStatusBadge o={row.original} /> },
  { id: 'status_detail', header: 'Detail', cell: ({ row }) => <StatusDetail o={row.original} /> },
  { id: 'shopify', header: 'Shopify', cell: ({ row }) => <ShopifyCell o={row.original} /> },
  { accessorKey: 'total_amount', header: 'Total', cell: ({ row }) => <MoneyCell amount={row.original.total_amount} currency={row.original.currency} /> },
  { accessorKey: 'backordered_ea', header: 'Backordered EA', cell: ({ row }) => row.original.backordered_ea
    ? <span className="font-medium text-warning-700">{row.original.backordered_ea}</span> : '' },
  { accessorKey: 'total_refunded', header: 'Refunded', cell: ({ row }) => Number(row.original.total_refunded ?? 0)
    ? <MoneyCell amount={row.original.total_refunded} currency={row.original.currency} /> : '' },
  { accessorKey: 'net_amount', header: 'Net', cell: ({ row }) => <MoneyCell amount={row.original.net_amount} currency={row.original.currency} /> },
  { accessorKey: 'review_reason', header: 'Review reason', cell: ({ row }) => (
    <span className="line-clamp-2 max-w-md whitespace-pre-line text-xs text-neutral-600">{row.original.review_reason ?? ''}</span>
  ) },
]

/** A list of orders with the order's detail in a sheet; Review orders carry
 *  Re-check in the list and the detail (FR-OB-15). With `fixed` filters (a
 *  list inside another page) it has no views. */
export function OrderList({ fixed, title, description, emptyTitle, detailActions, detailTabs, toolbar, create }: {
  fixed?: QuerySpec['filters']; title: string; description: string; emptyTitle: string
  /** More buttons in the order sheet's action bar. */
  detailActions?: (o: OrderRead, close: () => void) => React.ReactNode
  /** Tabs before the order's own (e.g. Confirm delivery). */
  detailTabs?: (o: OrderRead, close: () => void) => RecordTab[]
  toolbar?: React.ReactNode | ((ctx: { rows: OrderRead[]; refresh: () => Promise<void> }) => React.ReactNode)
  create?: ListCreate<OrderRead>
}) {
  return (
    <ListPage<OrderRead>
      resource={ordersClient}
      title={title}
      description={description}
      emptyTitle={emptyTitle}
      fixed={fixed}
      columns={orderColumns}
      toolbar={toolbar ? (ctx) => (typeof toolbar === 'function' ? toolbar(ctx) : toolbar) : undefined}
      create={create}
      rowActions={(o) => (o.fulfillment_status === 'Review' ? <Recheck o={o} compact />
        : isDraft(o) ? <SubmitDraft o={o} /> : null)}
      detailSize="xl"
      renderDetail={(o, close) => (
        <OrderSheet o={o} close={close} extraActions={detailActions?.(o, close)} extraTabs={detailTabs?.(o, close)} />
      )}
    />
  )
}

const isDraft = (o: OrderRead) => o.source_type === 'MANUAL' && o.fulfillment_status === 'Draft'

/** Orders: every order from Shopify, retailer 850s and manual entry. Its
 *  system views include Needs review (the old Review worklist) and Manual
 *  drafts (the old Manual orders page, 2026-11-02); New manual order opens the
 *  standard sheet. */
export default function OrdersPage() {
  return <OrderList title="Orders" description="Orders from the Shopify store, retailer 850s and manual entry. Choose a view (e.g. Needs review, Manual drafts), or search, sort and group the list yourself." emptyTitle="No orders here" toolbar={<NextSends />}
    create={{ label: 'New manual order', permission: 'sales.order.operate', className: 'max-w-3xl',
      render: (done, cancel) => <ManualOrderForm onSaved={done} onCancel={cancel} /> }}
    detailActions={(o) => <ReturnAfterShipment o={o} />} />
}
