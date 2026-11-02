import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/orders.py (hand-written, see partnersApi.ts).
export const FULFILLMENT_STATUSES = ['Draft', 'Review', 'Payment Hold', 'Pending', 'Archive Suppressed', '940 Generated',
  '940 Sent', '940 Accepted', '940 Rejected', '940 Timeout', '940 Failed', 'Shipped', 'On Hold', 'Cancelled'] as const
export const SOURCE_TYPES = ['SHOPIFY', 'EDI_850', 'MANUAL'] as const

export interface OrderRead {
  id: number
  order_number: number
  partner_code: string
  source_type: (typeof SOURCE_TYPES)[number]
  source_order_id: string
  source_order_number: string | null
  source_reference: string | null
  order_date: string | null
  requested_delivery_date: string | null
  ship_to: Record<string, string | null> | null
  currency: string
  total: string | null
  fulfillment_status: string
  invoice_status: string
  order_state: 'Draft' | 'Active' | 'On Hold' | 'Completed' | 'Cancelled'
  hold_reason: string | null
  review_reason: string | null
  historical: boolean
  first_received_at: string
  paid_at: string | null
  archived_in_shopify_at: string | null
  archive_suppressed: boolean
  shopify_financial_status: string | null
  source_updated_at: string | null
  version: number
  created_at: string
  updated_at: string | null
}

export interface LineRead {
  id: number
  line_no: number
  item_code: string | null
  source: Record<string, unknown>
  ordered_qty: string
  order_unit: string | null
  factor_ea: string | null
  qty_ea: number | null
  unit_price: string | null
  line_status: string
  removed: boolean
  problem: string | null
}

/** A source document, or a 940 sent to the warehouse: its file status and
 *  its 997 status are separate (Sent is not Accepted, FR-OB-22). */
export interface OrderDocument {
  id: number
  kind: string
  direction: 'IN' | 'OUT'
  remote_name: string | null
  created_at: string
  file_status: string | null
  ack_status: string | null
  st02: string | null
  sent_at: string | null
  error: string | null
  ack_detail: string | null
}

export interface ShipmentRowRead {
  lx_index: number
  w12_index: number
  sscc: string | null
  item_code: string | null
  lot: string | null
  expiry: string | null
  qty_ordered_ea: number | null
  qty_shipped_ea: number
  tracking: string | null
  needs_review: boolean
}

/** A 945: applied, or kept (a second 945, a shadow one), with its checks
 *  (FR-OB-25d) and the Shopify write-back (FR-OB-11). */
export interface ShipmentRead {
  id: number
  document_id: number
  order_key_received: string | null
  ship_date: string | null
  ship_date_source: string | null
  carrier: string | null
  mode: string | null
  tracking: string[] | null
  bol: string | null
  applied: boolean
  shadow: boolean
  checks: { check: string; message: string }[] | null
  shopify_status: 'Not needed' | 'Pending' | 'Done' | 'Failed' | 'Suppressed'
  shopify_error: string | null
  rows: ShipmentRowRead[]
}

export interface OrderDetail extends OrderRead {
  notes: string | null
  lines: LineRead[]
  documents: OrderDocument[]
  shipments: ShipmentRead[]
  timeline: { at: string; user_name: string; entity: string; action: string; before: Record<string, unknown> | null;
    after: Record<string, unknown> | null; reason: string | null }[]
}

export interface ManualLine { item_code: string; quantity: number; unit: string }
export interface ManualOrderIn {
  partner_code: string
  customer_reference?: string | null
  requested_delivery_date?: string | null
  ship_to?: Record<string, string> | null
  notes?: string | null
  lines: ManualLine[]
}
export interface LinePreview { item_code: string; quantity: string; unit: string; qty_ea: number | null; problem: string | null }

export const ordersClient = createResourceClient<OrderRead, never>(api, '/orders')
export const fetchOrder = (id: number) => api.get<OrderDetail>(`/orders/${id}`)
export const resolveReview = (id: number) => api.post<{ order: OrderRead; remaining: string[] }>(`/orders/${id}/resolve-review`)
export const previewManual = (lines: ManualLine[]) => api.post<LinePreview[]>('/orders/manual/preview', lines)
export const createManual = (data: ManualOrderIn) => api.post<OrderRead>('/orders/manual', data)
export const updateManual = (id: number, data: ManualOrderIn, version: number) => api.put<OrderRead>(`/orders/manual/${id}?version=${version}`, data)
export const submitManual = (id: number, version: number) => api.post<OrderRead>(`/orders/manual/${id}/submit?version=${version}`)
export const cancelManual = (id: number, version: number) => api.post<OrderRead>(`/orders/manual/${id}/cancel?version=${version}`)

export interface Send940Result { order: OrderRead; document_id: number; file_status: string; control: string; errors: string[] }
export const send940 = (id: number, replace = false) => api.post<Send940Result>(`/orders/${id}/send-940${replace ? '?replace=true' : ''}`)
export interface EligibleOrder { order_id: number; order_number: number; partner_code: string; source_order_number: string | null }
export const fetchEligibility = () => api.get<EligibleOrder[]>('/orders/eligibility-preview')
export const retryShopify = (shipmentId: number) =>
  api.post<{ shipment_id: number; shopify_status: string }>(`/orders/shipments/${shipmentId}/shopify-retry`)
