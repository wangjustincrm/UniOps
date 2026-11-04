import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/orders.py (hand-written, see partnersApi.ts).
export const FULFILLMENT_STATUSES = ['Draft', 'Review', 'Payment Hold', 'Pending', 'Archive Suppressed', '940 Generated',
  '940 Sent', '940 Accepted', '940 Rejected', '940 Timeout', '940 Failed', 'Shipped', 'On Hold', 'Cancelled'] as const
export const SOURCE_TYPES = ['SHOPIFY', 'EDI_850', 'MANUAL', 'FROM_945'] as const
export const SOURCE_LABEL: Record<string, string> = { SHOPIFY: 'Shopify', EDI_850: 'Retailer 850', MANUAL: 'Manual', FROM_945: 'Created from 945' }

export interface OrderRead {
  id: number
  order_number: number
  partner_code: string
  source_type: (typeof SOURCE_TYPES)[number]
  source_order_id: string
  source_order_number: string | null
  /** The Order ref (A.3): partner abbreviation + "-" + source number — the visible identifier. */
  source_reference: string | null
  /** The partner's name (lists show it; the code in its tooltip). */
  partner_name: string | null
  order_date: string | null
  requested_delivery_date: string | null
  ship_to: Record<string, string | null> | null
  currency: string
  total: string | null
  /** The source's total, else the lines at the PO price (what lists show and sum). */
  total_amount?: string | null
  fulfillment_status: string
  invoice_status: string
  /** Internal (D17): what eligibility reads. People see `order_status`. */
  order_state: 'Draft' | 'Active' | 'On Hold' | 'Completed' | 'Cancelled'
  shadow: boolean
  /** What people see, derived by sales-api (app/domain/order_status.py). */
  order_status: string
  on_hold_reason: string | null
  /** As shown: "On Hold: 997 Timeout". */
  order_status_label: string
  /** A mark beside the Status: the 945 (applied or the shadow run's) left something unshipped. */
  short_shipped: boolean
  /** Only what the Status does not say (a time, the hold's detail); usually empty. */
  status_detail: string | null
  status_detail_at: string | null
  shopify_fulfillment_status: string | null
  source_cancelled_at: string | null
  hold_reason: string | null
  review_reason: string | null
  historical: boolean
  first_received_at: string
  paid_at: string | null
  archived_in_shopify_at: string | null
  archive_suppressed: boolean
  shopify_financial_status: string | null
  // Shopify finance: the sales channel and tags, the original total, refunds and the net (original − refunded)
  source_name?: string | null
  tags?: string | null
  original_total?: string | null
  total_refunded?: string | null
  net_amount?: string | null
  /** What the applied 945 left unshipped, in EA (none before a 945). */
  backordered_ea?: number | null
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
  /** The line's own invoice price per order unit (null = the PO price). */
  invoice_unit_price?: string | null
  line_status: string
  removed: boolean
  problem: string | null
  shipped_ea?: number | null
  delivered_qty?: number | null
  /** Shopify: the line's taxes and discounts */
  tax_amount?: string | null
  discount_amount?: string | null
  // The detail's lifecycle: the invoice's lines for it, its state, the 945 rows packed for it.
  invoiced_qty?: number | null
  invoice_unit?: string | null
  /** Invoiced: the invoice's price per its unit (CA); before: the price the draft takes, per order unit. */
  invoice_price?: string | null
  invoice_amount?: string | null
  state?: string | null
  /** Ordered − shipped EA once an applied 945 set the shipped quantity. */
  backordered_ea?: number | null
  /** The shadow run's 945s for this line (Zoho is live): shown, never applied */
  shipped_shadow_ea?: number | null
  backordered_shadow_ea?: number | null
  packing?: PackingRead[]
}

/** A 945 row packed for an order line, with its shipment's date and tracking. */
export interface PackingRead {
  shipment_id: number
  document_id: number
  sscc: string | null
  lot: string | null
  expiry: string | null
  qty_shipped_ea: number
  tracking: string | null
  ship_date: string | null
  /** Applied, or kept only (a shadow-run or second 945). */
  applied: boolean
  shadow: boolean
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
  id: number
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
  // The line it was matched to, what the Item Master says, the parcel's link
  lx_number?: string | null
  upc?: string | null
  order_line_id?: number | null
  line_no?: number | null
  description?: string | null
  /** EA in a case: the cases shipped, when known */
  case_factor?: number | null
  tracking_url?: string | null
}

/** A tracking number and the carrier's link for it (none for an unknown carrier). */
export interface TrackingLinkRead {
  number: string
  url: string | null
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
  /** the 945's N9*E1: the reference the 940 was sent with */
  our_reference?: string | null
  applied: boolean
  shadow: boolean
  checks: { check: string; message: string }[] | null
  shopify_status: 'Not needed' | 'Pending' | 'Done' | 'Failed' | 'Suppressed'
  shopify_error: string | null
  rows: ShipmentRowRead[]
  // The 945 as received: its ship-to (N1 ST), the carrier by name, its control numbers, when it came
  ship_to?: Record<string, string | null> | null
  carrier_name?: string | null
  tracking_links?: TrackingLinkRead[]
  set_control?: string | null
  isa13?: string | null
  gs06?: string | null
  document_name?: string | null
  received_at?: string | null
  /** A split shipment's part ("a", "b"), from the order key's letter */
  part?: string | null
  /** The 997 Sales sent back for it (none: Sales does not send 997s for 945s) */
  ack_sent?: string | null
}

export interface OrderDetail extends OrderRead {
  /** Cancel order is open (the server's rule; never for a shadow order). */
  cancellable?: boolean
  /** Where Release hold can take the order: none when it is not On Hold. */
  hold_exits?: string[]
  bill_to: Record<string, string | null> | null
  /** The DC a retailer ship-to GLN routes to (Walmart). */
  dc_number: string | null
  /** Non-blocking warnings about the ship-to (a PO box). */
  ship_to_flags?: string[]
  notes: string | null
  subtotal?: string | null
  tax?: string | null
  shipping?: string | null
  discount?: string | null
  lines: LineRead[]
  /** The invoice draft exists: prices are changed on the invoice. */
  has_invoice?: boolean
  documents: OrderDocument[]
  shipments: ShipmentRead[]
  // Shopify finance: gateways, discount codes, tax lines, transactions and refunds (no card data)
  payment_gateways?: string[] | null
  discount_codes?: { code: string | null; type: string | null; amount: string }[] | null
  tax_lines?: { title: string | null; rate: number | null; amount: string }[] | null
  payments?: PaymentRead[]
  refunds?: RefundRead[]
  timeline: { at: string; user_name: string; entity: string; action: string; before: Record<string, unknown> | null;
    after: Record<string, unknown> | null; reason: string | null }[]
}

/** A Shopify transaction: sale, authorization, capture, refund or void. */
export interface PaymentRead {
  id: number
  shopify_id: number
  parent_id: number | null
  refund_id: number | null
  kind: string
  gateway: string | null
  status: string | null
  amount: string
  currency: string | null
  processed_at: string | null
  authorization: string | null
  payment_id: string | null
  error_code: string | null
  test: boolean
}

export interface RefundRead {
  id: number
  shopify_id: number
  refunded_at: string | null
  processed_at: string | null
  note: string | null
  restock: boolean | null
  lines_amount: string
  shipping_amount: string
  adjustments_amount: string
  /** Its successful refund transactions. */
  amount: string
  lines: { line_item_id: number | null; order_line_id: number | null; sku: string | null; title: string | null; quantity: number;
    subtotal: string; total_tax: string; restock_type: string | null }[]
  adjustments: { kind: string | null; reason: string | null; amount: string; tax_amount: string }[]
}

export interface ManualLine { item_code: string; quantity: number; unit: string }
export interface ManualOrderIn {
  partner_code: string
  customer_reference?: string | null
  requested_delivery_date?: string | null
  ship_to: Record<string, string>
  notes?: string | null
  lines: ManualLine[]
}
export interface LinePreview { item_code: string; quantity: string; unit: string; qty_ea: number | null; problem: string | null }

export const ordersClient = createResourceClient<OrderRead, never>(api, '/orders')
/** A 945 with no order and what making its order would do (FR-OB-25c). */
export interface OrderFrom945Plan {
  shipment_id: number
  document_id: number
  order_key: string | null
  reference: string | null
  partner_code: string | null
  partner_name_received: string | null
  po: string | null
  ship_date: string | null
  ship_to: Record<string, string | null> | null
  lines: { item_code: string | null; received: string; qty_ea: number; shipped_ea: number; problem: string | null }[]
  /** A later part: the order it joins. */
  order_id: number | null
  order_ref: string | null
  refused: string | null
  refused_message: string | null
}
export const fetchShipmentsWithoutOrder = (documentId: number) =>
  api.get<OrderFrom945Plan[]>(`/orders/shipments-without-order?document_id=${documentId}`)
export const createOrderFrom945 = (shipmentId: number, partnerCode: string | null) =>
  api.post<OrderRead>(`/orders/shipments/${shipmentId}/create-order`, { partner_code: partnerCode })
export const cancelOrder = (id: number, reason: string) => api.post<OrderRead>(`/orders/${id}/cancel`, { reason })
export const releaseHold = (id: number, to: string, reason: string) => api.post<OrderRead>(`/orders/${id}/release-hold`, { to, reason })
export const resolveRow = (shipmentId: number, rowId: number, orderLineId: number | null, reason: string | null) =>
  api.post<OrderRead>(`/orders/shipments/${shipmentId}/rows/${rowId}/resolve`, { order_line_id: orderLineId, reason })
export const fetchOrder = (id: number) => api.get<OrderDetail>(`/orders/${id}`)
export const resolveReview = (id: number) => api.post<{ order: OrderRead; remaining: string[] }>(`/orders/${id}/resolve-review`)
export const previewManual = (lines: ManualLine[]) => api.post<LinePreview[]>('/orders/manual/preview', lines)
export const createManual = (data: ManualOrderIn) => api.post<OrderRead>('/orders/manual', data)
export const updateManual = (id: number, data: ManualOrderIn, version: number) => api.put<OrderRead>(`/orders/manual/${id}?version=${version}`, data)
export const submitManual = (id: number, version: number) => api.post<OrderRead>(`/orders/manual/${id}/submit?version=${version}`)
export const cancelManual = (id: number, version: number) => api.post<OrderRead>(`/orders/manual/${id}/cancel?version=${version}`)

export interface Send940Result { order: OrderRead; document_id: number; file_status: string; control: string; errors: string[] }
export const send940 = (id: number, replace = false) => api.post<Send940Result>(`/orders/${id}/send-940${replace ? '?replace=true' : ''}`)
export interface EligibleOrder {
  order_id: number; order_number: number; source_reference: string | null; partner_code: string; source_order_number: string | null
}
export const fetchEligibility = () => api.get<EligibleOrder[]>('/orders/eligibility-preview')
/** One shipment's packing list (PDF): pallets → items → lots → expiry → qty, tracking and BOL. */
export const downloadPackingList = (orderId: number, shipmentId: number) =>
  api.download(`/orders/${orderId}/shipments/${shipmentId}/packing-list.pdf`, `PackingList-${shipmentId}.pdf`)
export interface InvoicePricesBody { reason: string | null; lines: { id: number; invoice_unit_price: string | null }[] }
export const setInvoicePrices = (id: number, body: InvoicePricesBody) => api.put<OrderDetail>(`/orders/${id}/invoice-prices`, body)
export const retryShopify = (shipmentId: number) =>
  api.post<{ shipment_id: number; shopify_status: string }>(`/orders/shipments/${shipmentId}/shopify-retry`)

/** The line number a person sees, by the line's line_no: an 850's PO line
 *  number as is; a Shopify order's position (1, 2, 3 in line_no order), since
 *  its line_no is Shopify's line-item id (kept in a tooltip, see LineNo). */
export function lineNumbers(o: Pick<OrderDetail, 'source_type' | 'lines'>): Map<number, number> {
  const nos = o.lines.map((ln) => ln.line_no)
  if (o.source_type !== 'SHOPIFY') return new Map(nos.map((n) => [n, n]))
  return new Map([...nos].sort((a, b) => a - b).map((n, i) => [n, i + 1]))
}
