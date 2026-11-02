import { createResourceClient } from '@/platform-ui'
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
  created_at: string
  updated_by_name: string | null
  order_number: number | null
  partner_code: string | null
  po: string | null
  status: string | null
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
}
export interface InvoiceDetail extends InvoiceRead { lines: InvoiceLineRead[]; confirmed_date: string | null }
export interface ConfirmResult { order_id: number; ok: boolean; invoice_id: number | null; status: string | null; error: string | null }

export const invoicesClient = createResourceClient<InvoiceRead, never>(api, '/invoices')
export const fetchInvoice = (id: number) => api.get<InvoiceDetail>(`/invoices/${id}`)
export const confirmDelivery = (body: { order_id: number; confirmed_date: string; lines: { order_line_id: number; delivered_qty: number }[]; note?: string | null }) =>
  api.post<InvoiceDetail>('/invoices/confirm', body)
export const confirmBulk = (order_ids: number[], confirmed_date: string) =>
  api.post<ConfirmResult[]>('/invoices/confirm-bulk', { order_ids, confirmed_date })
export const editInvoice = (id: number, version: number, body: { invoice_date?: string | null; lines: { id: number; qty?: number; unit_price?: string }[] }) =>
  api.put<InvoiceDetail>(`/invoices/${id}?version=${version}`, body)
export const sendInvoice = (id: number) => api.post<InvoiceDetail>(`/invoices/${id}/send`)
export const releaseInvoice = (order_id: number, reason: string) => api.post<{ order_id: number; invoice_status: string }>('/invoices/release', { order_id, reason })
export const recordReturn = (body: { order_id: number; kind: 'Cancelled after shipment' | 'Returned'; reason: string; lines: { order_line_id: number; qty_ea: number }[] }) =>
  api.post<{ return_id: number; invoice_status: string }>('/invoices/returns', body)
