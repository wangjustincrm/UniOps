import { api, fetchAllPages } from '@/lib/api'
import type { ApiEvent } from './pr'
import type { WorkflowNodeDef, CurrentStep } from '@/types'

export type PaStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'processed'
  | 'returned'
  | 'rejected'
  | 'cancelled'

export type PaType = 'regular' | 'prepayment' | 'settlement' | 'balance'

// Display labels for every PA type. Single source so detail/list views never
// fall back to a binary "prepayment vs Regular" check (which mislabels
// settlement/balance as "Regular Payment").
export const PA_TYPE_LABEL: Record<PaType, string> = {
  regular: 'Regular Payment',
  prepayment: 'Prepayment',
  settlement: 'Settlement',
  balance: 'Balance',
}

export type PaAction = 'submit' | 'approve' | 'return' | 'reject' | 'process' | 'cancel'

export type SettlementStatus = 'pending' | 'settled' | 'disputed'

export interface ApiPaLineItem {
  id: string
  po_line_id: string
  description: string
  qty: number
  unit: string
  unit_price: number
  line_total: number
  notes?: string
}

export interface ApiPa {
  id: string
  pa_number: string
  title: string
  pa_type: PaType
  prepayment_pa_id?: string | null
  prepayment_applied?: number | null
  status: PaStatus
  currency: string
  subtotal: number
  tax_amount: number
  tax_code?: string | null
  tax_rate?: number | null
  // Charge breakdown extras (Decimals arrive as strings — coerce with Number() at use sites).
  shipping_amount?: number | null
  other_charges?: number | null
  other_charges_note?: string | null
  payment_amount: number
  vendor_id: string
  vendor_name: string
  // Agreement-sourced PAs (Phase 1A) have no PO — the backend returns null for
  // both. Previously typed as non-nullable, which was a lie the moment
  // agreement-sourced PAs shipped (PaListPage.tsx renders po_number directly;
  // a null renders as blank, which is fine, but callers doing po_id-truthy
  // checks need the real type).
  po_id: string | null
  po_number: string | null
  // Every PO this payment settles, primary first — one PA may cover several
  // POs of the same vendor. po_id/po_number above are just the primary one, so
  // anything rendering "which PO(s)" must use these. Empty on the agreement
  // route. Optional in the type because a cached response from before the
  // multi-PO release has neither field.
  po_ids?: string[]
  po_numbers?: string[]
  // Agreement-sourced PAs (Phase 1A) carry these instead — both null for a
  // PO-sourced PA. Mirrors PaResponse.agreement_id/agreement_number
  // (epms-api/app/schemas/pa.py) — added when the agreement create route
  // (Task 10) needed to distinguish "this PA belongs to my agreement" from
  // the po_id/po_number pair, which the backend leaves null on this route.
  agreement_id?: string | null
  agreement_number?: string | null
  invoice_ids: string[]
  prepayment_pct?: number
  expected_settlement_date?: string
  settlement_status?: SettlementStatus
  settled_at?: string
  settled_by?: string
  settled_by_name?: string
  settlement_note?: string
  settlement_variance?: number
  line_items: ApiPaLineItem[]
  notes?: string
  approval_step_idx: number
  created_by_name?: string | null
  created_at: string
  updated_at: string
  current_step?: CurrentStep | null
}

export interface CreatePaBody {
  title: string
  pa_type: PaType
  currency: string
  subtotal: number
  tax_amount: number
  tax_code?: string | null
  tax_rate?: number | null
  shipping_amount?: number
  other_charges?: number
  other_charges_note?: string
  vendor_id: string
  vendor_name: string
  // Exactly one of po_id / agreement_id must be set — the backend 422s on a
  // body with neither or both (PaCreate._exactly_one_source in
  // epms-api/app/schemas/pa.py). The agreement route (Task 10) has no PO,
  // no GR, and no receipt-override fields — none of those apply there.
  po_id?: string
  // Multi-PO payment: every PO this PA settles, primary first. Sent instead of
  // po_id; the backend derives the primary from the first entry. All POs must
  // share a vendor and a currency, and the type must be 'regular'.
  po_ids?: string[]
  agreement_id?: string
  invoice_ids?: string[]
  gr_ids?: string[]
  prepayment_pa_id?: string
  prepayment_applied?: number
  prepayment_pct?: number
  expected_settlement_date?: string
  // Posted lines omit line_total — the backend recomputes it as qty × unit_price.
  line_items?: Omit<ApiPaLineItem, 'id' | 'line_total'>[]
  notes?: string
  // Receipt gate — set when a non-prepayment PA is submitted without a matched,
  // GR-backed invoice. Requires pa_override_receipt permission; backend re-checks.
  // Never sent on the agreement route (no receipt gate exists there).
  receipt_override?: boolean
  receipt_override_reason?: string | null
}

export interface UpdatePaBody {
  title?: string
  // NOTE: pa_type and currency are NOT editable — pa_type is derived from the
  // linked PO (prepaid PO → prepayment/settlement) and currency follows the PO.
  subtotal?: number
  tax_amount?: number
  tax_code?: string | null
  tax_rate?: number | null
  shipping_amount?: number
  other_charges?: number
  payment_amount?: number
  vendor_id?: string
  vendor_name?: string
  po_id?: string
  // Replaces the PA's whole PO set (draft / returned only). Omit to leave the
  // POs alone; [] is rejected — a PA must cover at least one PO.
  po_ids?: string[]
  invoice_ids?: string[]
  gr_ids?: string[]
  prepayment_applied?: number
  prepayment_pct?: number
  expected_settlement_date?: string
  // Posted lines omit line_total — the backend recomputes it as qty × unit_price.
  line_items?: Omit<ApiPaLineItem, 'id' | 'line_total'>[]
  notes?: string
}

export interface PaActionBody {
  action: PaAction
  comment?: string
  bank_account_id?: string   // funding bank/card for action='process'
  /**
   * Vendor credits to net off this payment (action='process' only).
   * THREE-VALUED — the wire contract mirrors finance-api's
   * PaymentExecuteRequest.credit_ids:
   *   omitted / undefined -> finance-api applies its automatic FIFO default
   *   []                  -> apply no credit at all this run
   *   [ids]               -> apply only these
   * Send it ONLY when the operator actually deselected a credit the preview
   * offered. Sending a full id list for an untouched dialog would work today
   * but would freeze a stale plan if a credit landed between preview and
   * confirm.
   */
  credit_ids?: string[]
}

export interface PaFilters {
  status?: PaStatus
  pa_type?: PaType
  department_id?: string
  vendor_id?: string
  po_id?: string
  search?: string
  page?: number
  page_size?: number
}

export interface PaListResponse {
  items: ApiPa[]
  total: number
}

export const paService = {
  list: async (filters?: PaFilters): Promise<PaListResponse> => {
    const raw = await api.get<PaListResponse | ApiPa[]>('/pa', filters)
    if (Array.isArray(raw)) return { items: raw, total: raw.length }
    return raw
  },

  listAll: (filters?: Omit<PaFilters, 'page' | 'page_size'>): Promise<PaListResponse> =>
    fetchAllPages((page, page_size) => paService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiPa>(`/pa/${id}`),

  create: (body: CreatePaBody) =>
    api.post<ApiPa>('/pa', body),

  update: (id: string, body: UpdatePaBody) =>
    api.patch<ApiPa>(`/pa/${id}`, body),

  action: (id: string, body: PaActionBody) =>
    api.post<ApiPa>(`/pa/${id}/action`, body),

  // Finance sign-off for a zero-cash (net-0) settlement with a variance (overpaid):
  // reconciles the prepayment + closes the invoice, no payment made.
  confirmSettlement: (id: string) =>
    api.post<ApiPa>(`/pa/${id}/confirm-settlement`, {}),

  events: (id: string) =>
    api.get<ApiEvent[]>(`/pa/${id}/events`),

  workflowSteps: (id: string) =>
    api.get<WorkflowNodeDef[]>(`/pa/${id}/workflow-steps`),
}
