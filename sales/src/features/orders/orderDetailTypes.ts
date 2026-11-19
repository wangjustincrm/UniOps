// Mirrors sales-api app/schemas/orders.py: an order's shipments, payments and refunds, and the manual order's body.
import type { LineRead, OrderDocument, OrderRead } from './orderTypes'

export interface ShipmentRowRead {
  id: number
  /** A row to resolve: the order lines of its own item (the only ones it can be linked to). */
  matching_lines?: number[]
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
  /** What Send 940 offers (sales-api decides): nothing once the goods have left. */
  send_940?: 'send' | 'replace' | null
  /** Where Release hold can take the order: none when it is not On Hold. */
  hold_exits?: string[]
  bill_to: Record<string, string | null> | null
  /** The DC a retailer ship-to GLN routes to (Walmart). */
  dc_number: string | null
  /** The buyer's TrueCommerce mailbox from its 850 (Sobeys), sent back in the 810. */
  edi_mailbox?: string | null
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

/** How a manual order ships (user 2026-11-13). */
export const CARRIERS = [{ value: 'Purolator', label: 'Purolator' }, { value: 'Purolator Express', label: 'Purolator Express' }, { value: 'LTL', label: 'LTL' }]
export type Carrier = 'Purolator' | 'Purolator Express' | 'LTL'
export const isCarrier = (v: unknown): v is Carrier => v === 'Purolator' || v === 'Purolator Express' || v === 'LTL'
/** The carriers a manual order may take, as the settings say (Purolator Express may be off). */
export const carriersFor = (expressOffered: boolean) => CARRIERS.filter((c) => expressOffered || c.value !== 'Purolator Express')
export interface ManualLine { item_code: string; quantity: number; unit: string }
export interface ManualOrderIn {
  partner_code: string
  customer_reference?: string | null
  requested_delivery_date?: string | null
  ship_to: Record<string, string>
  notes?: string | null
  lines: ManualLine[]
  /** How it ships (user 2026-11-13): Purolator (parcel) or LTL; required to submit. */
  carrier?: Carrier | null
}
export interface LinePreview { item_code: string; quantity: string; unit: string; qty_ea: number | null; problem: string | null; weight_lb?: string | null }
