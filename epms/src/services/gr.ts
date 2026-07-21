import { api, fetchAllPages } from '@/lib/api'

export type GrStatus =
  | 'pending_ack'
  | 'collection_pending'
  | 'collected'
  | 'confirmed'
  | 'discrepancy'
  | 'rejected'
  | 'cancelled'

export type GrAction = 'acknowledge' | 'collect' | 'confirm' | 'reject' | 'discrepancy' | 'cancel'

export type GrLineCondition = 'good' | 'discrepancy' | 'damaged'

export interface ApiGrLineItem {
  id: string
  po_line_id: string
  description: string
  material_id?: string
  qty_ordered: number
  qty_received: number
  unit: string
  unit_price: number
  line_total: number
  condition: GrLineCondition
  discrepancy_notes?: string
  actual_qty?: number
}

export interface ApiGr {
  id: string
  number: string
  title: string
  gr_type: 'physical' | 'service'
  status: GrStatus
  currency: string
  po_id: string
  po_number: string
  vendor_id: string
  vendor_name: string
  storage_location?: string
  line_items: ApiGrLineItem[]
  received_by: string
  received_at: string
  notes?: string
  acknowledged_at?: string
  acknowledged_by?: string
  collected_at?: string
  collected_by?: string
  collection_notes?: string
  created_at: string
}

export interface GrAttachmentIn {
  filename: string
  content_type: string
  data: string  // base64
}

export interface CreateGrBody {
  title: string
  // NOTE: gr_type and vendor_id are NOT sent — the backend derives both from the
  // linked PO. line_total is likewise omitted (recomputed as qty × unit_price).
  currency: string
  po_id: string
  storage_location?: string
  received_by?: string
  line_items: Omit<ApiGrLineItem, 'id' | 'line_total'>[]
  notes?: string
  attachments?: GrAttachmentIn[]
}

export interface GrActionBody {
  action: GrAction
  acknowledged_by?: string
  collected_by?: string
  comment?: string
  collection_notes?: string
}

export interface GrFilters {
  status?: GrStatus
  gr_type?: 'physical' | 'service'
  po_id?: string
  vendor_id?: string
  search?: string
  page?: number
  page_size?: number
}

export interface GrListResponse {
  items: ApiGr[]
  total: number
}

export const grService = {
  list: (filters?: GrFilters) =>
    api.get<GrListResponse>('/gr', filters),

  listAll: (filters?: Omit<GrFilters, 'page' | 'page_size'>): Promise<GrListResponse> =>
    fetchAllPages((page, page_size) => grService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiGr>(`/gr/${id}`),

  create: (body: CreateGrBody) =>
    api.post<ApiGr>('/gr', body),

  action: (id: string, body: GrActionBody) =>
    api.post<ApiGr>(`/gr/${id}/action`, body),
}
