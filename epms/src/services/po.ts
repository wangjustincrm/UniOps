import { api, fetchAllPages } from '@/lib/api'
import type { ApiEvent } from './pr'
import type { WorkflowNodeDef, CurrentStep } from '@/types'

export type PoStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'returned'
  | 'rejected'
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
  // Buyer-supplied sample requirement, e.g. "500 g" / "2 ea" (NC-imported POs).
  sample?: string | null
  // Source PR line this PO line was created from (backend persists it).
  pr_line_id?: string | null
  qty: number
  unit: string
  unit_price: number
  line_total: number
  received_qty: number
  notes?: string
  // 该 line 被其他发票累计分摊的税前额(仅 match-candidates 端点返回)
  already_allocated?: string | null
}

// Fields the client posts per line. Server derives line_total/received_qty;
// pr_line_id is kept so PO lines stay linked to their originating PR line.
type PoLineItemInput = Omit<
  ApiPoLineItem,
  'id' | 'line_total' | 'received_qty' | 'already_allocated'
>

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
  // Buyer-supplied detail on NC-imported POs. `notes` stays NC-owned and may
  // contain internal [NC Paid] / [NC Closed] markers — never show it as
  // buyer text on an NC PO.
  buyer_notes?: string | null
  incoterms?: string | null
  buyer_edited_at?: string | null
  pr_id?: string
  pr_number?: string
  pr_requester_id?: string | null
  approval_step_idx: number
  line_items: ApiPoLineItem[]
  created_by_name?: string | null
  created_at: string
  updated_at: string
  // 该 PO 被其他发票累计分摊的税前总额(仅 match-candidates 端点返回)
  already_allocated_total?: string | null
  current_step?: CurrentStep | null
  // NC ERP provenance — 'nc' for POs mirrored from NC purchase orders, null/undefined
  // for POs created natively in UniOps.
  source?: string | null
}

export interface CreatePoBody {
  title: string
  type: number
  currency: string
  tax_rate: number
  tax_code?: string | null
  vendor_id: string
  is_prepaid?: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  pr_id?: string
  // subtotal/tax_amount/total/vendor_name are recomputed/derived server-side.
  line_items: PoLineItemInput[]
}

export interface UpdatePoBody {
  title?: string
  type?: number
  currency?: string
  tax_rate?: number
  tax_code?: string | null
  vendor_id?: string
  is_prepaid?: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  // subtotal/tax_amount/total/vendor_name are recomputed/derived server-side.
  line_items?: PoLineItemInput[]
}

export interface ImportedDetailsLineBody {
  id: string
  supplier_item_id?: string | null
  sample?: string | null
}

export interface ImportedDetailsBody {
  expected_delivery?: string | null
  delivery_address?: string | null
  incoterms?: string | null
  tax_code?: string | null
  tax_rate?: number | null
  is_prepaid?: boolean | null
  buyer_notes?: string | null
  lines: ImportedDetailsLineBody[]
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
  pr_type?: number
  department_id?: string
  is_prepaid?: boolean
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

  updateImportedDetails: (id: string, body: ImportedDetailsBody) =>
    api.patch<ApiPo>(`/po/${id}/imported-details`, body),

  action: (id: string, body: PoActionBody) =>
    api.post<ApiPo>(`/po/${id}/action`, body),

  events: (id: string) =>
    api.get<ApiEvent[]>(`/po/${id}/events`),

  placeOrder: (id: string, body: PlaceOrderBody) =>
    api.post<ApiPo>(`/po/${id}/place-order`, body),

  workflowSteps: (id: string) =>
    api.get<WorkflowNodeDef[]>(`/po/${id}/workflow-steps`),
}
