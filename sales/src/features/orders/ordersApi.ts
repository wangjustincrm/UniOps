import { createResourceClient, type FileSource } from '@/platform-ui'
import { api } from '@/lib/api'
import type { OrderRead } from './orderTypes'
import type { Carrier, OrderDetail, ManualLine, ManualOrderIn, LinePreview } from './orderDetailTypes'

// The wire types live in orderTypes.ts and orderDetailTypes.ts; every import from here keeps working.
export * from './orderTypes'
export * from './orderDetailTypes'

export const ordersClient = createResourceClient<OrderRead, never>(api, '/orders')
export const cancelOrder = (id: number, version: number, reason: string) => api.post<OrderRead>(`/orders/${id}/cancel?version=${version}`, { reason })
export const releaseHold = (id: number, version: number, to: string, reason: string) =>
  api.post<OrderRead>(`/orders/${id}/release-hold?version=${version}`, { to, reason })
export const resolveRow = (shipmentId: number, rowId: number, orderLineId: number | null, reason: string | null) =>
  api.post<OrderRead>(`/orders/shipments/${shipmentId}/rows/${rowId}/resolve`, { order_line_id: orderLineId, reason })
export const fetchOrder = (id: number) => api.get<OrderDetail>(`/orders/${id}`)
export const resolveReview = (id: number) => api.post<{ order: OrderRead; remaining: string[] }>(`/orders/${id}/resolve-review`)
export const previewManual = (lines: ManualLine[]) => api.post<LinePreview[]>('/orders/manual/preview', lines)
export const createManual = (data: ManualOrderIn) => api.post<OrderRead>('/orders/manual', data)
export const updateManual = (id: number, data: ManualOrderIn, version: number) => api.put<OrderRead>(`/orders/manual/${id}?version=${version}`, data)
export const submitManual = (id: number, version: number) => api.post<OrderRead>(`/orders/manual/${id}/submit?version=${version}`)
export const cancelManual = (id: number, version: number) => api.post<OrderRead>(`/orders/manual/${id}/cancel?version=${version}`)
/** Reorder for this customer (user 2026-11-19): a Draft manual order copied from this one, no lines. */
export const reorderOrder = (id: number) => api.post<OrderRead>(`/orders/${id}/reorder`)


export interface Send940Result { order: OrderRead; document_id: number; file_status: string; control: string; errors: string[] }
export const send940 = (id: number, replace = false) => api.post<Send940Result>(`/orders/${id}/send-940${replace ? '?replace=true' : ''}`)
/** Fulfil in Shopify now (user 2026-11-16): the 997 step again — the whole order, no tracking, then archived. */
export const fulfilInShopify = (id: number) => api.post<{ order_id: number; queued: boolean }>(`/orders/${id}/shopify-fulfil`)
/** The order's printable files (2026-11-12): each one looked at in FileViewer first, then downloaded or printed there. */
export const poPdf = (o: Pick<OrderRead, 'id' | 'source_order_number' | 'order_number'>): FileSource => ({
  key: `po-${o.id}`, title: `Purchase order ${o.source_order_number ?? o.order_number}`,
  load: () => api.file(`/orders/${o.id}/po.pdf`, `PO-${o.source_order_number ?? o.order_number}.pdf`) })
export const invoicePdf = (invoiceId: number, number: string): FileSource => ({
  key: `invoice-${invoiceId}`, title: `Invoice ${number}`, load: () => api.file(`/invoices/${invoiceId}/invoice.pdf`, `${number}.pdf`) })
/** One shipment's packing list (PDF): pallets → items → lots → expiry → qty, tracking and BOL. */
export const packingListPdf = (orderId: number, shipmentId: number, label = ''): FileSource => ({
  key: `packing-${shipmentId}`, title: `Packing list${label ? ` ${label}` : ''}`,
  load: () => api.file(`/orders/${orderId}/shipments/${shipmentId}/packing-list.pdf`, `PackingList-${shipmentId}.pdf`) })
/** Every stored file and PDF of the order in one zip: saved, not shown. */
export const downloadPacket = (o: Pick<OrderRead, 'id' | 'source_reference' | 'order_number'>) =>
  api.download(`/orders/${o.id}/packet.zip`, `Order-${o.source_reference ?? o.order_number}.zip`)
export interface InvoicePricesBody { reason: string | null; lines: { id: number; invoice_unit_price: string | null }[] }
/** `version`: the order as the person saw it; changed meanwhile → refused (entity.stale). */
export const setInvoicePrices = (id: number, version: number, body: InvoicePricesBody) =>
  api.put<OrderDetail>(`/orders/${id}/invoice-prices?version=${version}`, body)
/** What the customer returned per line, with why (user 2026-11-12): until the invoice is sent. */
export interface ReturnedBody { reason: string; lines: { id: number; returned_ea: number }[] }
export const setReturned = (id: number, version: number, body: ReturnedBody) => api.put<OrderDetail>(`/orders/${id}/returned?version=${version}`, body)
/** An order sent wrong, corrected and sent again (user 2026-11-13): until the warehouse ships it. */
export interface AmendBody { reason: string; carrier?: Carrier | null; lines: { id: number; item_code?: string; ordered_qty?: string; removed?: boolean }[] }
export const amendOrder = (id: number, version: number, body: AmendBody) =>
  api.post<{ order: OrderRead; sent: boolean; document_id: number | null }>(`/orders/${id}/amend?version=${version}`, body)
export const retryShopify = (shipmentId: number) =>
  api.post<{ shipment_id: number; shopify_status: string }>(`/orders/shipments/${shipmentId}/shopify-retry`)

/** The line number a person sees, by the line's line_no: an 850's PO line
 *  number as is; a Shopify order's position (1, 2, 3 in line_no order), since
 *  its line_no is Shopify's line-item id (kept in a tooltip, see LineNo). */
export function lineNumbers(o: Pick<OrderDetail, 'source_type' | 'lines'>): Map<number, number> {
  const nos = o.lines.map((ln) => ln.line_no)
  if (o.source_type !== 'SHOPIFY') return new Map(nos.map((n) => [n, n]))
  return new Map([...nos].sort((a, b) => a - b).map((n, i) => [n, i + 1]))
}

/** Manual order settings (user 2026-11-13 / 2026-11-19; Orders → Manual order settings): Express offered, the LTL
 *  suggestion weight, and why a manual order is made. */
export interface ManualOrderSettings { express_offered: boolean; ltl_from_lb: number; reasons: string[] }
export const MANUAL_SETTINGS_KEY = ['sales', 'config', 'manual-order-settings']
export const fetchManualOrderSettings = () => api.get<ManualOrderSettings>('/config/manual-order-settings')
export const saveManualOrderSettings = (s: ManualOrderSettings) => api.put<ManualOrderSettings>('/config/manual-order-settings', s)
