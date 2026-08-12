import { api, fetchAllPages } from '@/lib/api'
import type { ApiPo } from '@/services/po'
import type { AgreementListResponse } from '@/services/agreement'
import type { ReceiptListResponse, ReceiptStatus } from '@/services/agreementReceipts'

export interface InvoiceLineItem {
  id?:          string
  description:  string
  quantity:     number
  unit:         string | null
  unit_price:   number
  line_total:   number
  non_po_fee?:  boolean          // marked as a non-PO fee (excluded from matching)
  non_po_note?: string | null
}

export type InvoiceStatus =
  | 'unmatched'
  | 'matched'
  | 'exception'
  | 'match_review'
  | 'approved'
  | 'paid'

export interface ApiInvoice {
  id: string
  internal_ref: string
  vendor_invoice_number: string
  vendor_id: string
  vendor_name: string
  amount: number
  tax_amount: number
  total_amount: number
  currency: string
  invoice_date: string
  due_date: string
  line_items: InvoiceLineItem[]
  status: InvoiceStatus
  po_id?: string
  po_number?: string
  gr_id?: string
  gr_number?: string
  gr_ids?: string[]
  // house_account with receipt_ids only: which pickup receipts this invoice claims —
  // JSONB array on the backend (Invoice.receipt_ids), null before any claim, never
  // an empty array (see crud/invoice.py: cleared to null on unclaim, never []).
  receipt_ids?: string[] | null
  // Free-text explanation for why the claimed receipts' total doesn't line up
  // with the invoice total (submitted by MatchPanel.tsx, stored separately
  // from legacy_settlement_reason — see models/invoice.py:76-78).
  receipt_variance_reason?: string | null
  match_assignee_id?: string | null
  match_assignee_name?: string | null
  matched_at?: string
  matched_by?: string
  matched_by_name?: string
  po_total?: number
  gr_value?: number
  variance?: number
  variance_pct?: number
  exception_reason?: string
  exception_resolved_at?: string
  exception_resolved_by?: string
  exception_resolved_by_name?: string
  exception_resolution?: string
  notes?: string
  allocations?: InvoiceAllocation[]
  file_name?: string
  file_size?: string
  uploaded_at: string
  uploaded_by: string
  uploaded_by_name?: string
  created_at: string
  // Agreement route (house-account / no-PO matching) — Phase 1A only.
  agreement_id?: string | null
  agreement_number?: string | null
  // house_account | recurring | milestone — snapshot of the linked
  // agreement's type, written at match time (Task 8 fix round 1). Readable
  // by anyone who can read this invoice at all — deliberately NOT sourced
  // from GET /agreements/{id} or /invoices/{id}/agreement-candidates, both
  // gated more narrowly than plain invoice read access (see
  // InvoiceReceiptsPanel.tsx's module docstring for the review finding this
  // fixed: those routes 403 for warehouse_staff/supervisor/cfo/vendor_manager/
  // erp_pa_officer, silently falling back to the wrong copy for house_account
  // invoices with no evidence recorded yet).
  agreement_type?: 'house_account' | 'recurring' | 'milestone' | null
  // Which route this invoice was matched through. Only ever written as "po" or
  // "agreement" by the backend (epms-api/app/crud/invoice.py) — null before
  // any match. Not the same as `status`; a PO-route invoice keeps match_route
  // "po" even after review/exception handling.
  match_route?: 'po' | 'agreement' | null
  // 1A has no receipt evidence at all: every agreement match is flagged and
  // must carry a reason. This is the escape-hatch health metric Finance watches.
  legacy_settlement?: boolean
  legacy_settlement_reason?: string | null
}

export interface CreateInvoiceBody {
  vendor_invoice_number: string
  vendor_id: string
  amount: number
  tax_amount: number
  currency: string
  invoice_date: string
  due_date: string
  line_items?: InvoiceLineItem[]
  notes?: string
}

export interface UpdateInvoiceBody {
  vendor_invoice_number?: string
  amount?: number
  tax_amount?: number
  currency?: string
  invoice_date?: string
  due_date?: string
  notes?: string
  line_items?: InvoiceLineItem[]
  gr_ids?: string[]
}

export interface InvoiceAllocation {
  id: string
  invoice_id: string
  invoice_line_id: string
  po_id: string
  po_line_id: string | null
  allocated_amount: number
  allocated_tax: number
  allocated_total: number
  variance: number | null
  variance_pct: number | null
  note: string | null
  po_number?: string | null
  po_line_description?: string | null
}

export interface AllocationInput {
  invoice_line_id: string
  po_id: string
  po_line_id?: string | null
  allocated_amount: number
  allocated_tax?: number
  note?: string
}

export interface NonPoLineInput {
  line_id: string
  note?:   string | null
}

export interface MatchInvoiceBody {
  allocations?:  AllocationInput[]
  non_po_lines?: NonPoLineInput[]
  po_id?:        string
  gr_id?:        string
  gr_ids?:       string[]
  po_line_ids?:  string[]
  reference_po_id?: string
  // Agreement route — takes priority over every PO field on the backend when
  // set (epms-api/app/schemas/invoice.py InvoiceMatchRequest). Task 6: this
  // is pure linkage for every agreement_type, including house_account —
  // mounting a receipt or declaring a no-evidence settlement moved off this
  // request entirely (invoice detail page, Task 7/8); InvoiceMatchRequest no
  // longer has receipt_ids / receipt_variance_reason / legacy_settlement_reason
  // fields to mirror here.
  agreement_id?: string
  // milestone only — which schedule row (stage) this invoice pays for. recurring
  // FIFO-claims its own row server-side and never reads this; house_account has
  // no schedule rows at all. See InvoiceMatchRequest.schedule_id (epms-api).
  schedule_id?: string
}

// Task 8: full-override body for PUT /invoices/{id}/receipts — receipt_ids is
// the COMPLETE set of receipts this invoice should hold after the call;
// anything currently held but not listed here is released back to `open` by
// the backend (crud/invoice.py set_receipts — release-then-reclaim, no diff).
export interface SetReceiptsBody {
  receipt_ids: string[]
  variance_reason?: string | null
}

export interface ResolveExceptionBody {
  resolution: 'accepted' | 'credit_note_requested'
  note?: string
}

export interface InvoiceFilters {
  status?: string
  vendor_id?: string
  po_id?: string
  agreement_id?: string
  search?: string
  page?: number
  page_size?: number
}

export interface InvoiceListResponse {
  items: ApiInvoice[]
  total: number
}

export const invoiceService = {
  list: (filters?: InvoiceFilters) =>
    api.get<InvoiceListResponse>('/invoices', filters as Record<string, string | number | boolean | null | undefined>),

  listAll: (filters?: Omit<InvoiceFilters, 'page' | 'page_size'>): Promise<InvoiceListResponse> =>
    fetchAllPages((page, page_size) => invoiceService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiInvoice>(`/invoices/${id}`),

  create: (body: CreateInvoiceBody) =>
    api.post<ApiInvoice>('/invoices', body),

  update: (id: string, body: UpdateInvoiceBody) =>
    api.patch<ApiInvoice>(`/invoices/${id}`, body),

  match: (id: string, body: MatchInvoiceBody) =>
    api.post<ApiInvoice>(`/invoices/${id}/match`, body),

  // Candidate POs for allocation (same vendor, open statuses) — authorized by
  // the invoice's match rights, NOT the caller's general PO scope, so task
  // assignees without related PRs still see them.
  matchCandidates: (id: string) =>
    api.get<{ items: ApiPo[]; total: number }>(`/invoices/${id}/match-candidates`),

  // Candidate agreements for the agreement route (same vendor, inside the
  // admission window — active, or expired-but-within-grace). Authorized
  // identically to matchCandidates, NOT by a generic agreement scope. The
  // server's admission window is the rule — do not filter further client-side.
  agreementCandidates: (id: string) =>
    api.get<AgreementListResponse>(`/invoices/${id}/agreement-candidates`),

  // Pickup receipts for one of THIS invoice's candidate agreements — invoice-
  // scoped counterpart to agreementReceiptService.list (services/agreementReceipts.ts),
  // which hits GET /agreements/{id}/receipts gated on epms.agreement.read. That
  // permission isn't granted to every role that can legitimately match an
  // invoice (review finding, Task 10 round 2 Finding B), so MatchPanel calls
  // THIS route instead — authorised identically to agreementCandidates
  // above (same backend helper, not a parallel implementation).
  agreementReceipts: (id: string, agreementId: string, status?: ReceiptStatus) =>
    api.get<ReceiptListResponse>(
      `/invoices/${id}/agreements/${agreementId}/receipts`,
      status ? { status } : undefined,
    ),

  // Assignee bounces the match assignment back to the assigner (note required).
  declineMatch: (id: string, note: string) =>
    api.post<ApiInvoice>(`/invoices/${id}/decline-match`, { note }),

  resolveException: (id: string, resolution: 'accepted' | 'credit_note_requested', note?: string) =>
    api.post<ApiInvoice>(`/invoices/${id}/exception`, { resolution, note }),

  delete: (id: string) =>
    api.delete<void>(`/invoices/${id}`),

  assignMatch: (id: string, userId: string) =>
    api.post<ApiInvoice>(`/invoices/${id}/assign-match`, { user_id: userId }),

  reviewMatch: (id: string, action: 'approve' | 'reject', note?: string) =>
    api.post<ApiInvoice>(`/invoices/${id}/match-review`, { action, note }),

  // Task 7/8: mounting receipt evidence is its own action on the invoice
  // detail page, separate from /match (Task 6 pulled it out of
  // InvoiceMatchRequest). Full-override semantics — see SetReceiptsBody.
  setReceipts: (id: string, body: SetReceiptsBody) =>
    api.put<ApiInvoice>(`/invoices/${id}/receipts`, body),

  // Explicit "no receipt evidence exists" declaration — releases any receipts
  // this invoice currently holds (crud/invoice.py settle_without_receipt).
  settleWithoutReceipt: (id: string, reason: string) =>
    api.post<ApiInvoice>(`/invoices/${id}/settle-without-receipt`, { reason }),
}
