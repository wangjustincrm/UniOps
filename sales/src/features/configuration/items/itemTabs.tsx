import type { ReactNode } from 'react'
import {
  ListPage, RecordHistory, columnsOf, useListCount, type ColumnDef, type ListScope, type QuerySpec, type RecordTab,
} from '@/platform-ui'
import { lotColumns, moveColumns } from '@/features/inventory/inventoryColumns'
import { stockLotsClient, stockMovesClient, type StockLotRead, type StockMoveRead } from '@/features/inventory/inventoryApi'
import { shippedClient, type ShippedRead } from '@/features/lots/lotsApi'
import { shippedColumns } from '@/features/lots/shippedColumns'
import { OrderList } from '@/features/orders/OrdersPage'
import { ordersClient } from '@/features/orders/ordersApi'
import { InvoiceList } from '@/features/invoices/InvoiceList'
import { invoicesClient } from '@/features/invoices/invoicesApi'
import { TransferList } from '@/features/transfers/TransferList'
import { transfersClient } from '@/features/transfers/transfersApi'
import { partnerCodesClient, type PartnerCodeRead } from '@/features/configuration/partner-codes/partnerCodesApi'
import { itemsClient, type ItemRead } from './itemsApi'

const FIT = 560  // a list inside a record: its own height, the record's header above it
const colCode = columnsOf<PartnerCodeRead>()
const partnerCodeColumns: ColumnDef<PartnerCodeRead, unknown>[] = [colCode.text('partner_code', 'Partner'), colCode.text('buyer_part', 'Buyer part')]
const withoutItem = <T,>(cols: ColumnDef<T, unknown>[]) => cols.filter((c) => (c as { accessorKey?: string }).accessorKey !== 'item_code')

/** An item's tabs: Overview, then each list that concerns it, scoped to the item (the server's lock) and counted on
 *  the server (each list's /count with the scope). A tab renders, and reads its rows, only when shown. */
export function useItemTabs(item: ItemRead, scope: ListScope, overview: ReactNode): RecordTab[] {
  const spec: QuerySpec = { page: 1, pageSize: 1, scope: scope.filters }
  const counts = {
    lots: useListCount(stockLotsClient, spec), moves: useListCount(stockMovesClient, spec), shipped: useListCount(shippedClient, spec),
    orders: useListCount(ordersClient, spec), invoices: useListCount(invoicesClient, spec), transfers: useListCount(transfersClient, spec),
    codes: useListCount(partnerCodesClient, spec),
  }
  return [
    { id: 'overview', label: 'Overview', render: () => overview },
    { id: 'lots', label: 'Lots', count: counts.lots, render: () => (
      <ListPage<StockLotRead> resource={stockLotsClient} columns={withoutItem(lotColumns)} scope={scope} fit={FIT} emptyTitle="No lot of this item at Radiant" />) },
    { id: 'moves', label: 'Stock card', count: counts.moves, render: () => (
      <ListPage<StockMoveRead> resource={stockMovesClient} columns={moveColumns} scope={scope} fit={FIT} emptyTitle="No stock move yet" />) },
    { id: 'shipped', label: 'Shipped', count: counts.shipped, render: () => (
      <ListPage<ShippedRead> resource={shippedClient} columns={withoutItem(shippedColumns)} scope={scope} fit={FIT} emptyTitle="Nothing of this item shipped yet" />) },
    { id: 'orders', label: 'Orders', count: counts.orders, render: () => <OrderList scope={scope} emptyTitle="No order has this item" /> },
    { id: 'invoices', label: 'Invoices', count: counts.invoices, render: () => <InvoiceList scope={scope} /> },
    { id: 'transfers', label: 'Transfers', count: counts.transfers, render: () => <TransferList scope={scope} /> },
    { id: 'codes', label: 'Partner codes', count: counts.codes, render: () => (
      <ListPage<PartnerCodeRead> resource={partnerCodesClient} columns={partnerCodeColumns} scope={scope} fit={FIT} emptyTitle="No partner code for this item" />) },
    { id: 'history', label: 'History', render: () => <RecordHistory resource={itemsClient} id={item.id} version={item.version} /> },
  ]
}
