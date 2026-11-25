import type { QueryClient, QueryKey } from '@tanstack/react-query'
import { invoicesClient } from '@/features/invoices/invoicesApi'
import { ordersClient } from '@/features/orders/ordersApi'
import { queryKeys } from '@/platform-ui'

/** What is read again when an order, one of its parts or its invoice was saved: each version the pages hold of them. An order's
 *  lines, returned and prices move the order (and a draft invoice); an invoice's step moves the order; the invoice sheet sends the
 *  order's version. One list, so no page of the same person sends the version before their own save. */
export const orderMoved = (id: number): QueryKey[] => [
  queryKeys.detail(ordersClient, id), queryKeys.list(ordersClient), queryKeys.list(invoicesClient), queryKeys.details(invoicesClient)]

/** The same, waited for: before anything can be pressed on what was read. `answered`: the save's answer is the order as it
 *  is now and the page has put it in place: it is not read a second time. */
export async function readMoved(qc: QueryClient, id: number, { answered = false } = {}): Promise<void> {
  await Promise.all(orderMoved(id).slice(answered ? 1 : 0).map((queryKey) => qc.invalidateQueries({ queryKey })))
}
