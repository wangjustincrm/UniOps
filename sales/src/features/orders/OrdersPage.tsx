import { ListPage, MoneyCell, columnsOf, type ColumnDef, type ListCreate, type QuerySpec, type RecordTab } from '@/platform-ui'
import { OrderSheet } from './OrderDetailView'
import { OrderStatusBadge, ShopifyCell, StatusDetail } from './OrderStatusCells'
import { ReturnAfterShipment } from '@/features/invoices/ReturnAfterShipment'
import { SubmitDraft } from './DraftActions'
import { ManualOrderForm } from './ManualOrderForm'
import { Recheck } from './Recheck'
import { ordersClient, type OrderRead } from './ordersApi'
import { PartnerCell } from '@/features/common/PartnerCell'

const col = columnsOf<OrderRead>()

const orderColumns: ColumnDef<OrderRead, unknown>[] = [
  { accessorKey: 'source_reference', header: 'Order ref', cell: ({ row }) => <span className="font-medium">{row.original.source_reference ?? ''}</span> },
  col.text('order_number', 'Internal no.'),
  { accessorKey: 'partner_name', header: 'Partner', cell: ({ row }) => <PartnerCell name={row.original.partner_name} code={row.original.partner_code} /> },
  col.text('source_order_number', 'Customer PO / ref.'),
  col.date('order_date', 'Ordered'),
  { accessorKey: 'order_status', header: 'Status', cell: ({ row }) => <OrderStatusBadge o={row.original} /> },
  { id: 'status_detail', header: 'Detail', cell: ({ row }) => <StatusDetail o={row.original} /> },
  { id: 'shopify', header: 'Shopify', cell: ({ row }) => <ShopifyCell o={row.original} /> },
  col.money('total_amount', 'Total', { currency: 'currency' }),
  { accessorKey: 'backordered_ea', header: 'Backordered EA', cell: ({ row }) => row.original.backordered_ea
    ? <span className="font-medium text-warning-700">{row.original.backordered_ea}</span> : '' },
  { accessorKey: 'total_refunded', header: 'Refunded', cell: ({ row }) => Number(row.original.total_refunded ?? 0)
    ? <MoneyCell amount={row.original.total_refunded} currency={row.original.currency} /> : '' },
  col.money('net_amount', 'Net', { currency: 'currency' }),
  { accessorKey: 'review_reason', header: 'Review reason', cell: ({ row }) => (
    <span className="line-clamp-2 max-w-md whitespace-pre-line text-xs text-neutral-600">{row.original.review_reason ?? ''}</span>
  ) },
  // A channel's own columns (user 2026-11-09): shown where a view names them (Open Shopify, Open retailers).
  col.datetime('first_received_at', 'Received'),
  col.status('shopify_financial_status', 'Payment'),
  col.date('delivery_due_date', 'Delivery due'),
  col.date('cancel_date', 'Cancel by'),  // the PO's cancel date; do-not-deliver-after is in Delivery due
  col.place('ship_to_place', 'Ship to', (o) => o.ship_to, { withName: (o) => o.source_type !== 'SHOPIFY' }),
]
/** Shown only where a view names them. */
const CHANNEL_COLUMNS = ['first_received_at', 'shopify_financial_status', 'delivery_due_date', 'cancel_date', 'ship_to_place']

/** A list of orders with the order's detail in a sheet; Review orders carry
 *  Re-check in the list and the detail (FR-OB-15). With `fixed` filters (a
 *  list inside another page) it has no views. */
const dueColumnIndex = orderColumns.findIndex((c) => 'accessorKey' in c && c.accessorKey === 'delivery_due_date')
const dueOrderColumns = [...orderColumns.slice(0, 5), orderColumns[dueColumnIndex],
  ...orderColumns.slice(5).filter((_, i) => i + 5 !== dueColumnIndex)]

export function OrderList({ fixed, title, description, emptyTitle, detailActions, detailTabs, toolbar, create, showDue }: {
  fixed?: QuerySpec['filters']; title: string; description: string; emptyTitle: string
  /** More buttons in the order sheet's action bar. */
  detailActions?: (o: OrderRead, close: () => void) => React.ReactNode
  /** Tabs before the order's own (e.g. Confirm delivery). */
  detailTabs?: (o: OrderRead, close: () => void) => RecordTab[]
  toolbar?: React.ReactNode | ((ctx: { rows: OrderRead[]; refresh: () => Promise<void> }) => React.ReactNode)
  create?: ListCreate<OrderRead>
  /** The Delivery due column (the delivery list). */
  showDue?: boolean
}) {
  return (
    <ListPage<OrderRead>
      resource={ordersClient}
      title={title}
      description={description}
      emptyTitle={emptyTitle}
      fixed={fixed}
      columns={showDue ? dueOrderColumns : orderColumns}
      defaultHidden={showDue ? CHANNEL_COLUMNS.filter((c) => c !== 'delivery_due_date') : CHANNEL_COLUMNS}
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
  return <OrderList title="Orders" description="Orders from the Shopify store, retailer 850s and manual entry. Choose a view (e.g. Needs review, Manual drafts), or search, sort and group the list yourself." emptyTitle="No orders here"
    create={{ label: 'New manual order', permission: 'sales.order.operate', className: 'max-w-3xl',
      render: (done, cancel) => <ManualOrderForm onSaved={done} onCancel={cancel} /> }}
    detailActions={(o) => <ReturnAfterShipment o={o} />} />
}
