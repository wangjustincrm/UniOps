import { api } from '@/lib/api'

/**
 * PO Fee Invoices — freight / customs invoices from forwarders and brokers,
 * linked to the NC PO arrival they belong to (raw material, packaging or milk). Paid by a
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
  /** What an Other fee is — required for that type, null otherwise. */
  note: string | null
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
  /** True for any order mirrored from NC, milk included, and any approved EPMS-raised PO. */
  applies: boolean
  /** False on an EPMS-raised PO: no Incoterms there, fees are only listed. */
  is_nc: boolean
  /** Raw-material / packaging orders must carry Incoterms; on milk it is optional. */
  incoterms_required: boolean
  incoterms_code: string | null
  incoterms_place: string | null
  term: { code: string; label: string; needs_freight: boolean; needs_customs: boolean; active: boolean } | null
  status: FeeStatus
  arrivals: PoFeeArrival[]
  /** Linked with no arrival: an EPMS-raised PO linked while it had none, or an arrival since deleted. */
  unassigned: PoFeeInvoice[]
}

export interface FeeLink {
  id: string
  invoice_line_id: string | null
  fee_type: FeeType
  note: string | null
  po_id: string
  po_number: string
  po_vendor_name: string
  gr_id: string | null
  gr_number: string | null
  gr_received_at: string | null
}

export interface FeeLinkBody {
  fee_type: FeeType
  /** Required when fee_type is 'other' (the server refuses it otherwise). */
  note?: string | null
  po_id: string
  gr_id?: string | null
  invoice_line_id?: string | null
}

export interface FeeCandidatePo {
  id: string
  number: string
  vendor_name: string
  status: string
  /** 'epms' = raised in EPMS; such a PO may have no arrival, and is then linked as a whole. */
  source: 'nc' | 'epms'
  /** Milk orders are linkable too (no Incoterms requirement on them). */
  is_milk: boolean
  incoterms_code: string | null
  arrivals: { id: string; number: string; received_at: string | null }[]
}

export interface FeeCandidatePage {
  /** How many POs match in all — the list may show fewer. */
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
  /** `fee_type` freight / customs leaves out milk orders with no Incoterms —
   *  those can only be linked as Other (the server refuses it too). */
  candidates: (q: string, limit: number, feeType: FeeType) =>
    api.get<FeeCandidatePage>('/invoices/fee-link-candidates',
      q ? { q, limit, fee_type: feeType } : { limit, fee_type: feeType }),
}
