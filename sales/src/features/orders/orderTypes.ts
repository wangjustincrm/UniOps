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
  /** Shopify's shipping line; a manual order's carrier (Purolator / LTL). */
  shipping_method?: string | null
  source_order_id: string
  source_order_number: string | null
  /** The Order ref (A.3): partner abbreviation + "-" + source number — the visible identifier. */
  source_reference: string | null
  /** The partner's name (lists show it; the code in its tooltip). */
  partner_name: string | null
  order_date: string | null
  requested_delivery_date: string | null
  /** The PO's last dates (retailers). */
  cancel_date?: string | null
  do_not_deliver_after?: string | null
  delivery_due_date?: string | null
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
  status_detail_kind: string | null
  /** What to do next, and whose turn it is (sales-api app/domain/status_help.py). */
  next_step?: string | null
  next_step_who?: 'you' | 'warehouse' | 'shopify' | 'nobody' | null
  /** When the partner's delay starts: at receipt (invoiced partners) or at payment (prepaid). */
  delay_starts_at?: string | null
  /** The partner's delay in minutes (0: none) and settlement (INVOICE: billed after delivery). */
  delay_minutes?: number | null
  settlement_mode?: string | null
  shopify_fulfillment_status: string | null
  /** The Shopify fulfillment made at the 997 (or Zoho's): its 945 adds the tracking to it. */
  shopify_fulfillment_id?: string | null
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
  /** What the applied 945 left unshipped, in EA (none before a 945): read only by the Short shipped mark. */
  backordered_ea?: number | null
  /** What the customer returned (or refused) of what shipped, over the lines; none when nothing. */
  returned_ea?: number | null
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
  /** Returned (or refused) by the customer, entered before the invoice goes; delivered = shipped − returned. */
  returned_ea?: number
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
