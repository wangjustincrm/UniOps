import { api } from '@/lib/api'
import { createResourceClient, type QuerySpec } from '@/platform-ui'

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
  partner_code: string | null
  partner_name: string | null
  ship_to_city: string | null
  ship_to_postal: string | null
}

export const lotsClient = createResourceClient<LotRead, never>(api, '/lots')
export const exportLots = (spec: QuerySpec) => api.download(lotsClient.exportPath(spec), 'lots.csv')

/** Lots pasted for a recall: one per line, or separated by commas, semicolons or spaces. */
export function parseLots(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean))]
}
