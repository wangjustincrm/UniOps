import { useParams } from 'react-router-dom'
import { ListPage, RecordSheet, formatDate, formatNumber, useRecordList, type Filter, type RecordFact } from '@/platform-ui'
import { lotColumns } from '@/features/lots/lotColumns'
import { lotsClient, type LotRead } from '@/features/lots/lotsApi'
import { RecordRoute } from './RecordRoute'

/** /lots/:lot — a lot in its own tab (stage 1 of the records work, user 2026-11-27): what it is and where it went —
 *  its item and expiry, how much shipped, to how many orders and customers — and every 945 row of it (Shipped).
 *  The recall view (customers, export) comes with stage 3. */
export default function LotRecordPage() {
  const lot = useParams().lot ?? ''
  const fixed: Filter[] = [{ field: 'lot', op: 'eq', value: lot }]
  // Counted on the server over every row of the lot: by order, and by customer.
  const byOrder = useRecordList(lotsClient, { page: 1, pageSize: 1, filters: fixed, group: [{ field: 'order_ref' }] }, { errors: 'inline' })
  const byCustomer = useRecordList(lotsClient, { page: 1, pageSize: 1, filters: fixed, group: [{ field: 'partner_name' }] }, { errors: 'inline' })
  const first: LotRead | undefined = byOrder.data?.items[0]
  const total = byOrder.data?.total ?? 0
  const shipped = byOrder.data?.totals?.[0]?.sums.qty_shipped_ea
  const facts: RecordFact[] = !byOrder.data ? [] : [
    { label: 'Item', value: first?.item_code ?? '—' },
    { label: 'Expiry', value: first?.expiry ? formatDate(first.expiry) : '—' },
    { label: 'Shipped EA', value: shipped != null ? formatNumber(Number(shipped)) : '—', hint: `${total} ${total === 1 ? 'row' : 'rows'} in 945s` },
    { label: 'Orders', value: byOrder.data.groups?.length ?? 0 },
    { label: 'Customers', value: byCustomer.data?.groups?.length ?? '—' },
  ]
  return (
    <RecordRoute type="lot" title={`Lot ${lot}`}>
      {() => (
        <RecordSheet title={lot} subtitle="Lot" record={{ type: 'lot', key: lot }} loading={byOrder.isLoading} error={byOrder.error}
          facts={facts}
          tabs={[{ id: 'shipped', label: 'Shipped', count: total, empty: total === 0 && 'No 945 has reported this lot.',
            render: () => <ListPage<LotRead> resource={lotsClient} columns={lotColumns} fixed={fixed} fit={560} emptyTitle="No 945 rows" /> }]}
        />
      )}
    </RecordRoute>
  )
}
