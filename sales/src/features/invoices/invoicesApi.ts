import type { TimelineEntry } from '@/features/common/recordFiles'
import { createResourceClient, type ActionOut } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/invoices.py (hand-written, see partnersApi.ts).
export interface InvoiceRead {
  id: number
  invoice_number: string
  order_id: number
  method: '810_JSON' | 'EMAIL' | 'NONE'
  invoice_date: string
  currency: string
  payment_terms: string | null
  subtotal: string
  total: string
  document_id: number | null
  sent_at: string | null
  send_count: number
  error: string | null
  version: number
  /** The order's version: Release says which one was seen. */
  order_version?: number | null
  created_at: string
  updated_by_name: string | null
  order_number: number | null
  partner_code: string | null
  po: string | null
  status: string | null
  /** The order's ref (A.3) and the partner's name. */
  order_ref: string | null
  partner_name: string | null
  /** The retailer's proof-of-delivery / receiving number (record and PDF; the 810 has no field for it). */
  delivery_number: string | null
}
export interface InvoiceLineRead {
  id: number
  line_no: number
  item_code: string | null
  buyer_part: string | null
  description: string | null
  uom: string
  qty: number
  unit_price: string
  amount: string
  /** The PO's price for one invoice unit (CASE), from the order line now; read only. */
  po_price: string | null
}
export interface InvoiceDetail extends InvoiceRead { lines: InvoiceLineRead[]; confirmed_date: string | null; timeline: TimelineEntry[] }
export interface ConfirmResult { order_id: number; ok: boolean; invoice_id: number | null; status: string | null; sent?: boolean; error: string | null }

export const invoicesClient = createResourceClient<InvoiceRead, never>(api, '/invoices')
export const fetchInvoice = (id: number) => api.get<InvoiceDetail>(`/invoices/${id}`)
/** `version`: the order as the person saw it; changed meanwhile (a returned saved) → refused (entity.stale). */
export const confirmDelivery = (body: { order_id: number; version: number; confirmed_date: string; lines: { order_line_id: number; delivered_qty: number }[]; note?: string | null; send?: boolean }) =>
  api.post<InvoiceDetail>('/invoices/confirm', body)
/** No date: each order on the date its delivery was due. Each by the version seen: one that changed meanwhile is left alone. */
export const confirmBulk = async (orders: { id: number; version: number }[], confirmed_date: string | null, send = false): Promise<ConfirmResult[]> => {
  const out = await api.post<ActionOut>('/orders/actions/confirm_delivery',
    { ids: Object.fromEntries(orders.map((o) => [o.id, o.version])), input: { confirmed_date, send } })
  return out.results.map((r) => ({ order_id: r.id, ok: r.status === 'done', invoice_id: null, status: null, sent: r.note === 'sent', error: r.status === 'done' ? null : r.reason ?? r.status }))
}
export interface InvoiceEditBody {
  invoice_date?: string | null
  delivery_number?: string | null
  /** Needed when a unit price changes; kept in the audit. */
  reason?: string | null
  lines: { id: number; qty?: number; unit_price?: string }[]
}
export const editInvoice = (id: number, version: number, body: InvoiceEditBody) =>
  api.put<InvoiceDetail>(`/invoices/${id}?version=${version}`, body)
/** By the version seen: an invoice that moved meanwhile is refused (entity.stale). */
export const sendInvoice = (id: number, version: number) => api.post<InvoiceDetail>(`/invoices/${id}/send?version=${version}`)
/** An invoice already sent, once more: with why (kept in its Activity). */
export const sendInvoiceAgain = (id: number, version: number, reason: string) =>
  api.post<InvoiceDetail>(`/invoices/${id}/send?version=${version}`, { again: true, reason })
/** `version`: the order's, as the person saw it; changed meanwhile → refused (entity.stale). */
export const releaseInvoice = (order_id: number, version: number, reason: string) =>
  api.post<{ order_id: number; invoice_status: string }>('/invoices/release', { order_id, version, reason })
export const recordReturn = (body: { order_id: number; version: number; kind: 'Cancelled after shipment' | 'Returned'; reason: string; lines: { order_line_id: number; qty_ea: number }[] }) =>
  api.post<{ return_id: number; invoice_status: string }>('/invoices/returns', body)
