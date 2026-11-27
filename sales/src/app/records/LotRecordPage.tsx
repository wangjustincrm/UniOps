import { useParams } from 'react-router-dom'
import {
  ListPage, RecordSheet, RefLink, formatDate, formatNumber, useListCount, useListMeta, useRecordList, type BarItem, type ListScope,
  type QuerySpec, type RecordFact,
} from '@/platform-ui'
import { lotColumns } from '@/features/lots/lotColumns'
import { RECALL_FILE, downloadRecall, lotsClient, shippedClient, type LotRead, type ShippedRead } from '@/features/lots/lotsApi'
import { recallColumns } from '@/features/lots/shippedColumns'
import { moveColumns } from '@/features/inventory/inventoryColumns'
import { stockLotsClient, stockMovesClient, type StockMoveRead } from '@/features/inventory/inventoryApi'
import { RecordRoute } from './RecordRoute'

const FIT = 560

/** /lots/:lot — a lot's recall page (stage 3 of the records work, user 2026-11-27): which customers and orders got it,
 *  how much, when, delivered or not (from the applied 945s), with the recall's CSV (its own key); every 945 row of it
 *  (a kept-only one too); and its stock card. Each tab the list itself, scoped to the lot (the server's lock). */
export default function LotRecordPage() {
  const lot = useParams().lot ?? ''
  const scope: ListScope = { kind: 'lot', label: `Lot = ${lot}`, filters: [{ field: 'lot', op: 'eq', value: lot }] }
  const spec = (group?: string): QuerySpec => ({ page: 1, pageSize: 1, scope: scope.filters, ...(group ? { group: [{ field: group }] } : {}) })
  // Counted on the server over every shipped row of the lot: by order, and by customer.
  const byOrder = useRecordList(shippedClient, spec('order_ref'), { errors: 'inline' })
  const byCustomer = useRecordList(shippedClient, spec('customer'), { errors: 'inline' })
  const stock = useRecordList(stockLotsClient, spec(), { errors: 'inline' })
  const rows945 = useListCount(lotsClient, spec())
  const moves = useListCount(stockMovesClient, spec())
  const recall = useListMeta(shippedClient).data?.capabilities?.files?.find((f) => f.path === RECALL_FILE)
  const first: ShippedRead | undefined = byOrder.data?.items[0]
  const shipped = byOrder.data?.totals?.[0]?.sums.qty_shipped_ea
  const onHand = stock.data?.items[0]?.on_hand_ea
  const facts: RecordFact[] = !byOrder.data ? [] : [
    { label: 'Item', value: first?.item_code ? <RefLink record={{ type: 'item', key: first.item_code }} from={shippedClient}>{first.item_code}</RefLink> : '—' },
    { label: 'Expiry', value: first?.expiry ? formatDate(first.expiry) : '—' },
    { label: 'Shipped EA', value: shipped != null ? formatNumber(Number(shipped)) : '0', hint: 'applied 945s' },
    { label: 'Customers', value: byCustomer.data?.groups?.length ?? '—', hint: `${byOrder.data.groups?.length ?? 0} orders` },
    { label: 'On hand EA', value: onHand != null ? formatNumber(onHand) : '—', hint: 'at Radiant' },
  ]
  const bar: BarItem[] = [
    { id: 'recall-csv', label: 'Export for a recall (CSV)', primary: 10, group: 'record', changes: false, refreshes: false,
      fit: recall?.allowed ? true : 'You do not hold the recall export', whenNotFit: 'hidden', done: 'Recall exported', failPrefix: 'Export failed',
      hint: 'Every customer and order this lot went to: customer, order, ship date, EA, delivery', onSelect: () => downloadRecall([lot]) },
  ]
  return (
    <RecordRoute type="lot" title={`Lot ${lot}`}>
      {() => (
        <RecordSheet title={lot} subtitle="Lot" record={{ type: 'lot', key: lot }} loading={byOrder.isLoading} error={byOrder.error}
          facts={facts} bar={bar}
          tabs={[
            { id: 'customers', label: 'Customers', count: byOrder.data?.total, empty: byOrder.data?.total === 0 && 'No applied 945 has shipped this lot.',
              render: () => <ListPage<ShippedRead> resource={shippedClient} columns={recallColumns} scope={scope} fit={FIT} emptyTitle="Not shipped" /> },
            { id: 'rows', label: 'All 945 rows', count: rows945,
              render: () => <ListPage<LotRead> resource={lotsClient} columns={lotColumns} scope={scope} fit={FIT} emptyTitle="No 945 rows" /> },
            { id: 'moves', label: 'Stock card', count: moves,
              render: () => <ListPage<StockMoveRead> resource={stockMovesClient} columns={moveColumns} scope={scope} fit={FIT} emptyTitle="No stock move" /> },
          ]}
        />
      )}
    </RecordRoute>
  )
}
