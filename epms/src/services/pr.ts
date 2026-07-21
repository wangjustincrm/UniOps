import { api, fetchAllPages } from '@/lib/api'
import type { WorkflowNodeDef } from '@/types'

export type PrStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'returned'
  | 'rejected'
  | 'cancelled'

export type PrAction = 'submit' | 'approve' | 'return' | 'reject' | 'cancel' | 'recall'

export interface ApiPrLineItem {
  id: string
  description: string
  material_id?: string
  supplier_item_id?: string
  qty: number
  unit: string
  unit_price: number
  line_total: number
  notes?: string
}

export interface ApiPr {
  id: string
  number: string
  title: string
  type: number
  status: PrStatus
  currency: string
  amount: number
  vendor_id?: string
  vendor_name?: string
  is_prepaid: boolean
  cost_center_id?: string
  cost_center_name?: string
  department_name?: string
  budget_code?: string
  factor_combo?: Record<string, string> | null
  project_code?: string | null
  required_by?: string
  delivery_address?: string
  notes?: string
  over_budget: boolean
  over_budget_justification?: string | null
  submitted_at?: string
  approval_step_idx: number
  line_items: ApiPrLineItem[]
  po_id?: string
  po_number?: string
  created_by_name?: string | null
  created_at: string
  updated_at: string
}

export interface CreatePrBody {
  title: string
  type: number
  currency: string
  vendor_id?: string
  is_prepaid?: boolean
  project_code?: string
  cost_center_id?: string
  budget_code?: string
  factor_combo?: Record<string, string>
  required_by?: string
  delivery_address?: string
  notes?: string
  over_budget_justification?: string
  // amount/vendor_name/line_total are recomputed server-side.
  line_items: Omit<ApiPrLineItem, 'id' | 'line_total'>[]
}

export interface UpdatePrBody {
  title?: string
  type?: number
  currency?: string
  vendor_id?: string
  cost_center_id?: string
  budget_code?: string
  factor_combo?: Record<string, string> | null
  required_by?: string
  delivery_address?: string
  notes?: string
  is_prepaid?: boolean
  over_budget_justification?: string
  // amount/vendor_name/line_total are recomputed server-side.
  line_items?: Omit<ApiPrLineItem, 'id' | 'line_total'>[]
}

export interface PrActionBody {
  action: PrAction
  comment?: string
}

export interface PrFilters {
  status?: PrStatus
  pr_type?: number
  department_id?: string
  is_prepaid?: boolean
  created_by?: string
  search?: string
  page?: number
  page_size?: number
}

export interface PrListResponse {
  items: ApiPr[]
  total: number
}

export interface ApiEvent {
  id: string
  step_idx: number
  action: string
  actor_id: string
  actor_role: string
  actor_name?: string | null
  comment?: string | null
  created_at: string
}

export const prService = {
  list: (filters?: PrFilters) =>
    api.get<PrListResponse>('/pr', filters),

  listAll: (filters?: Omit<PrFilters, 'page' | 'page_size'>): Promise<PrListResponse> =>
    fetchAllPages((page, page_size) => prService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiPr>(`/pr/${id}`),

  create: (body: CreatePrBody) =>
    api.post<ApiPr>('/pr', body),

  update: (id: string, body: UpdatePrBody) =>
    api.patch<ApiPr>(`/pr/${id}`, body),

  action: (id: string, body: PrActionBody) =>
    api.post<ApiPr>(`/pr/${id}/action`, body),

  events: (id: string) =>
    api.get<ApiEvent[]>(`/pr/${id}/events`),

  workflowSteps: (id: string) =>
    api.get<WorkflowNodeDef[]>(`/pr/${id}/workflow-steps`),
}
