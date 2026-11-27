import { api } from '@/lib/api'
import { createResourceClient } from '@/platform-ui'

/** A 945 row as a recall sees it: the lot, its shipment and the order and customer it went to. */
export interface LotRead {
  id: number
  lot: string | null
  item_code: string | null
  expiry: string | null
  days_to_expiry: number | null
  qty_shipped_ea: number
  sscc: string | null
  tracking: string | null
  /** The 945's carrier (SCAC) and its link for this parcel (Configuration → Carriers) */
  carrier?: string | null
  tracking_url?: string | null
  shipment_id: number
  document_id: number
  ship_date: string | null
  bol: string | null
  /** the 945's N9*E1: the reference the 940 was sent with */
  our_reference: string | null
  /** false: kept only (a shadow-run or second 945) */
  applied: boolean
  shadow: boolean
  order_id: number | null
  order_ref: string | null
  /** The order's Internal no. */
  order_number?: number | null
  partner_code: string | null
  partner_name: string | null
  ship_to_city: string | null
  ship_to_postal: string | null
}

export const lotsClient = createResourceClient<LotRead, never>(api, '/lots')

/** An applied 945 row (Shipped): what went out to a customer, with the customer (a Shopify one as an initial and the
 *  city) and whether its delivery is confirmed. */
export interface ShippedRead extends LotRead {
  customer: string | null
  delivery_confirmed: boolean
}

export const shippedClient = createResourceClient<ShippedRead, never>(api, '/shipped')

/** The recall file (`/shipped/recall/customers.csv`, its own key): every customer and order these lots went to. */
export const RECALL_FILE = 'recall/customers.csv'
export const downloadRecall = (lots: string[]) => api.download(
  shippedClient.exportPath({ page: 1, pageSize: 1, scope: [{ field: 'lot', op: 'in', value: lots }] }, RECALL_FILE),
  lots.length === 1 ? `recall-${lots[0]}.csv` : `recall-${lots.length}-lots.csv`)

/** Lots pasted for a recall: one per line, or separated by commas, semicolons or spaces. */
export function parseLots(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean))]
}
