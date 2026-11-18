import {
  useRecordList,
} from '@/platform-ui'
import { invoicesClient } from '@/features/invoices/invoicesApi'
import type { OrderDetail } from './ordersApi'

/** The order's invoice (one per order), shared by the Invoice and Documents tabs. */
export function useOrderInvoice(o: Pick<OrderDetail, 'id' | 'has_invoice'>) {
  return useRecordList(invoicesClient, o.has_invoice
    ? { page: 1, pageSize: 1, filters: [{ field: 'order_id', op: 'eq', value: String(o.id) }] } : null, { errors: 'inline' })
}
