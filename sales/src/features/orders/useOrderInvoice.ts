import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '@/platform-ui'
import { invoicesClient } from '@/features/invoices/invoicesApi'
import type { OrderDetail } from './ordersApi'

/** The order's invoice (one per order), shared by the Invoice and Documents tabs. */
export function useOrderInvoice(o: Pick<OrderDetail, 'id' | 'has_invoice'>) {
  return useQuery({
    queryKey: [...queryKeys.list(invoicesClient), 'order', o.id],
    queryFn: () => invoicesClient.list({ page: 1, pageSize: 1, filters: [{ field: 'order_id', op: 'eq', value: String(o.id) }] }),
    enabled: !!o.has_invoice,
  })
}
