import { useParams } from 'react-router-dom'
import { RecordSheet, useRecord } from '@/platform-ui'
import { OrderSheet } from '@/features/orders/OrderDetailView'
import { fetchOrder, ordersClient } from '@/features/orders/ordersApi'
import { orderName } from '@/features/orders/orderActions'
import { RecordRoute } from './RecordRoute'

/** /orders/:id — an order in its own tab: the same view as the Orders list's sheet (OrderSheet). */
export default function OrderRecordPage() {
  const id = Number(useParams().id)
  // The same read as the view's own (one query key): read once.
  const q = useRecord(ordersClient, id, fetchOrder, { errors: 'inline' })
  return (
    <RecordRoute type="order" title={q.data ? orderName(q.data) : undefined}>
      {(close) => (q.data
        ? <OrderSheet o={q.data} close={close} />
        : <RecordSheet title={`Order #${id}`} loading={!q.error} error={q.error} tabs={[]} />)}
    </RecordRoute>
  )
}
