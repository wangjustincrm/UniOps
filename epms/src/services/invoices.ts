import { api, fetchAllPages } from '@/lib/api'
import type { ApiPo } from '@/services/po'

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
}

export interface ResolveExceptionBody {
  resolution: 'accepted' | 'credit_note_requested'
  note?: string
}

export interface InvoiceFilters {
  status?: string
  vendor_id?: string
  po_id?: string
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
}
