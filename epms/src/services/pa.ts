import { api, fetchAllPages } from '@/lib/api'
import type { ApiEvent } from './pr'
import type { WorkflowNodeDef } from '@/types'

export type PaStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'processed'
  | 'returned'
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
  payment_amount: number
  vendor_id: string
  vendor_name: string
  po_id: string
  po_number: string
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
  po_id: string
  invoice_ids?: string[]
  gr_ids?: string[]
  prepayment_pa_id?: string
  prepayment_applied?: number
  prepayment_pct?: number
  expected_settlement_date?: string
  line_items?: Omit<ApiPaLineItem, 'id'>[]
  notes?: string
}

export interface UpdatePaBody {
  title?: string
  pa_type?: PaType
  currency?: string
  subtotal?: number
  tax_amount?: number
  tax_code?: string | null
  tax_rate?: number | null
  payment_amount?: number
  vendor_id?: string
  vendor_name?: string
  po_id?: string
  invoice_ids?: string[]
  prepayment_applied?: number
  prepayment_pct?: number
  expected_settlement_date?: string
  line_items?: Omit<ApiPaLineItem, 'id'>[]
  notes?: string
}

export interface PaActionBody {
  action: PaAction
  comment?: string
  bank_account_id?: string   // funding bank/card for action='process'
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
