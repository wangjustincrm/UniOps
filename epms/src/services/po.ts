import { api, fetchAllPages } from '@/lib/api'
import type { ApiEvent } from './pr'

export type PoStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'issued'
  | 'partially_received'
  | 'fully_received'
  | 'closed'
  | 'cancelled'

export type PoAction = 'submit' | 'approve' | 'return' | 'reject' | 'issue' | 'cancel'

export interface ApiPoLineItem {
  id: string
  description: string
  material_id?: string
  supplier_item_id?: string
  qty: number
  unit: string
  unit_price: number
  line_total: number
  received_qty: number
  notes?: string
}

export interface ApiPo {
  id: string
  number: string
  title: string
  type: number
  status: PoStatus
  currency: string
  subtotal: number
  tax_rate: number
  tax_code?: string | null
  tax_amount: number
  total: number
  vendor_id: string
  vendor_name: string
  is_prepaid: boolean
  has_unpaid_invoice: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  pr_id?: string
  pr_number?: string
  pr_requester_id?: string | null
  approval_step_idx: number
  line_items: ApiPoLineItem[]
  created_by_name?: string | null
  created_at: string
  updated_at: string
}

export interface CreatePoBody {
  title: string
  type: number
  currency: string
  subtotal: number
  tax_rate: number
  tax_code?: string | null
  tax_amount: number
  total: number
  vendor_id: string
  vendor_name: string
  is_prepaid?: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  pr_id?: string
  line_items: Omit<ApiPoLineItem, 'id'>[]
}

export interface UpdatePoBody {
  title?: string
  type?: number
  currency?: string
  subtotal?: number
  tax_rate?: number
  tax_code?: string | null
  tax_amount?: number
  total?: number
  vendor_id?: string
  vendor_name?: string
  is_prepaid?: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  line_items?: Omit<ApiPoLineItem, 'id'>[]
}

export interface PoActionBody {
  action: PoAction
  comment?: string
}

export interface PlaceOrderBody {
  method: 'email' | 'online'
  // email method
  to?: string
  cc?: string
  subject?: string
  body?: string
  // online method
  reference?: string
}

export interface PoFilters {
  status?: PoStatus
  type?: number
  vendor_id?: string
  pr_id?: string
  search?: string
  page?: number
  page_size?: number
}

export interface PoListResponse {
  items: ApiPo[]
  total: number
}

export const poService = {
  list: (filters?: PoFilters) =>
    api.get<PoListResponse>('/po', filters),

  listAll: (filters?: Omit<PoFilters, 'page' | 'page_size'>): Promise<PoListResponse> =>
    fetchAllPages((page, page_size) => poService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiPo>(`/po/${id}`),

  create: (body: CreatePoBody) =>
    api.post<ApiPo>('/po', body),

  update: (id: string, body: UpdatePoBody) =>
    api.patch<ApiPo>(`/po/${id}`, body),

  action: (id: string, body: PoActionBody) =>
    api.post<ApiPo>(`/po/${id}/action`, body),

  events: (id: string) =>
    api.get<ApiEvent[]>(`/po/${id}/events`),

  placeOrder: (id: string, body: PlaceOrderBody) =>
    api.post<ApiPo>(`/po/${id}/place-order`, body),
}
