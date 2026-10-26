import { api } from '@/lib/api'

/**
 * PO Fee Invoices — freight / customs invoices from forwarders and brokers,
 * linked to the NC raw-material PO (and arrival) they belong to. Paid by a
 * Direct PA as before; the link is a record of where the cost came from and
 * never changes the invoice's match, status or payment.
 * Backend: epms-api app/crud/po_fee.py.
 */

export type FeeType = 'freight' | 'customs' | 'other'

/** Roll-up for a raw-material PO against what its Incoterms expect. */
export type FeeStatus = 'complete' | 'missing' | 'no_arrivals' | 'not_required' | 'not_set'

export const FEE_STATUS_LABEL: Record<FeeStatus, string> = {
  complete: 'Complete',
  missing: 'Missing',
  no_arrivals: 'No arrivals yet',
  not_required: 'Not required',
  not_set: 'Incoterms not set',
}

export const FEE_TYPE_LABEL: Record<FeeType, string> = {
  freight: 'Freight',
  customs: 'Customs',
  other: 'Other',
}

export interface PoFeeInvoice {
  link_id: string
  fee_type: FeeType
  invoice_id: string
  internal_ref: string
  vendor_invoice_number: string
  vendor_name: string
  invoice_date: string
  status: string
  currency: string
  total_amount: number | string
  /** Set when a single line of the invoice (a customs invoice) is linked. */
  line_description: string | null
  line_total: number | string | null
}

export interface PoFeeArrival {
  gr_id: string
  gr_number: string
  received_at: string | null
  freight: PoFeeInvoice[]
  customs: PoFeeInvoice[]
  other: PoFeeInvoice[]
}

export interface PoFeeDetail {
  /** False for anything but an NC raw-material / packaging order (milk excluded). */
  applies: boolean
  incoterms_code: string | null
  incoterms_place: string | null
  term: { code: string; label: string; needs_freight: boolean; needs_customs: boolean; active: boolean } | null
  status: FeeStatus
  arrivals: PoFeeArrival[]
  /** Linked while the PO had no arrival — counts toward none. */
  unassigned: PoFeeInvoice[]
}

export interface FeeLink {
  id: string
  invoice_line_id: string | null
  fee_type: FeeType
  po_id: string
  po_number: string
  po_vendor_name: string
  gr_id: string | null
  gr_number: string | null
  gr_received_at: string | null
}

export interface FeeLinkBody {
  fee_type: FeeType
  po_id: string
  gr_id?: string | null
  invoice_line_id?: string | null
}

export interface FeeCandidatePo {
  id: string
  number: string
  vendor_name: string
  status: string
  incoterms_code: string | null
  arrivals: { id: string; number: string; received_at: string | null }[]
}

export interface FeeCandidatePage {
  /** How many raw-material POs match in all — the list may show fewer. */
  total: number
  /** Latest arrival first; orders with no arrival yet last. */
  items: FeeCandidatePo[]
}

export const poFeeService = {
  poFeeStatus: (poId: string) => api.get<PoFeeDetail>(`/po/${poId}/fee-status`),
  invoiceLinks: (invoiceId: string) => api.get<FeeLink[]>(`/invoices/${invoiceId}/fee-links`),
  /** Replaces the invoice's COMPLETE link set — an empty list removes them all. */
  saveInvoiceLinks: (invoiceId: string, links: FeeLinkBody[]) =>
    api.put<FeeLink[]>(`/invoices/${invoiceId}/fee-links`, { links }),
  candidates: (q: string, limit: number) =>
    api.get<FeeCandidatePage>('/invoices/fee-link-candidates', q ? { q, limit } : { limit }),
}
