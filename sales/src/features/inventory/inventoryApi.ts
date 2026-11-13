import { api } from '@/lib/api'
import { createResourceClient, type QuerySpec } from '@/platform-ui'

/** One item at Radiant (design §15): every figure is a sum of its moves. */
export interface StockItemRead {
  id: number
  item_code: string
  description: string | null
  /** Its open lots only (a lot with nothing on hand is closed). */
  received_ea: number
  returned_ea: number
  shipped_ea: number
  adjusted_ea: number
  /** Sales − Radiant at the latest applied report (its open lots). */
  report_diff_ea: number
  on_hand_ea: number
  /** As Radiant's latest applied report gave it. */
  hold_ea: number
  /** On 940s that went out, not answered by a 945 yet. */
  allocated_ea: number
  available_ea: number
  /** Sent on 943s and not received; below zero: received and never sent (a discrepancy). */
  to_radiant_ea: number
  to_customer_ea: number
  /** Returned by customers, not back at Radiant; below zero: returns received with none recorded. */
  returning_ea: number
  lots: number
  first_expiry: string | null
  days_to_first_expiry: number | null
  last_move_at: string | null
}

export interface StockLotRead {
  id: number
  item_code: string
  lot: string | null
  expiry: string | null
  days_to_expiry: number | null
  /** 944 rows (transfers). */
  received_ea: number
  /** RA 944s: back from customers (already taken off Shipped). */
  returned_ea: number
  /** 945 rows and orders shipped with no 945, less what customers sent back. */
  shipped_ea: number
  /** People's adjustments after the latest applied report. */
  adjusted_ea: number
  /** Sales − Radiant at the latest applied report. */
  report_diff_ea: number
  on_hand_ea: number
  hold_ea: number
  /** On hand − on hold. */
  available_ea: number
  to_customer_ea: number
  returning_ea: number
  to_radiant_ea: number
  first_move_at: string | null
  last_move_at: string | null
}

export interface StockMoveRead {
  id: number
  at: string
  item_code: string
  lot: string | null
  expiry: string | null
  qty_ea: number
  from_place: string
  to_place: string
  source_type: string
  order_id: number | null
  transfer_id: number | null
  document_id: number | null
  reason: string | null
  created_by_name: string | null
}

export const stockItemsClient = createResourceClient<StockItemRead, never>(api, '/inventory/items')
export const stockLotsClient = createResourceClient<StockLotRead, never>(api, '/inventory/lots')
export const stockMovesClient = createResourceClient<StockMoveRead, never>(api, '/inventory/moves')
export const exportStock = (client: { exportPath: (s: QuerySpec) => string }, spec: QuerySpec, name: string) => api.download(client.exportPath(spec), name)

export interface AdjustBody { item_code: string; lot: string | null; qty_ea: number; reason: string }
export const adjustStock = (body: AdjustBody) => api.post<StockMoveRead>('/inventory/adjust', body)

/** Where a move went, in words. */
export const PLACE: Record<string, string> = {
  PLANT: 'Plant', TO_RADIANT: 'In transit to Radiant', RADIANT: 'Radiant', TO_CUSTOMER: 'In transit to customer',
  CUSTOMER: 'Customer', RETURNING: 'Returning', ADJUSTED: 'Adjustment',
}
export const SOURCE: Record<string, string> = {
  transfer_line: 'Transfer sent (943)', transfer_sent: 'Transfer sent (943)', transfer_accepted: 'Transfer difference accepted', receipt_row: 'Received (944)', return_944: 'Return received (RA 944)', return_order: 'Returned by customer (RA)',
  shipment_row: 'Shipped (945)', order_line: 'Shipped (order, no 945)', delivery: 'Delivered', returned: 'Returned by customer',
  adjustment: 'Adjustment', count: 'Radiant count (start)',
}

/** Radiant's inventory report at a moment (user 2026-11-13): compared lot by lot, then applied as the start. */
export interface StockCountRead {
  id: number
  counted_at: string
  source: string
  note: string | null
  file_name: string | null
  document_id: number | null
  /** Set: every lot starts from Radiant's figure at `counted_at`. */
  applied_at: string | null
  applied_by_name: string | null
  lines: number
  total_ea: number
  hold_ea: number
  version: number
  created_at: string
  created_by_name: string
}

export interface StockCountLineRead {
  id: number; item_code: string; lot: string | null; expiry: string | null; qty_ea: number; hold_ea: number; description: string | null
}

export type CompareKind = 'match' | 'differs' | 'sales_only' | 'radiant_only' | 'no_lot'

/** One item and lot: Sales' moves up to the count, Radiant's figure, the difference (Sales − Radiant), after it. */
export interface CountCompareRow {
  item_code: string
  lot: string | null
  description: string | null
  known_item: boolean
  expiry: string | null
  days_to_expiry: number | null
  /** Received (944) + returned (RA), up to the report. */
  in_ea: number
  received_ea: number
  returned_ea: number
  shipped_ea: number
  adjusted_ea: number
  sales_ea: number
  radiant_ea: number
  hold_ea: number
  in_report: boolean
  difference_ea: number
  in_after_ea: number
  out_after_ea: number
  since_ea: number
  /** Total now: Radiant + in after − out after. */
  now_ea: number
  /** At the report: Radiant − on hold. */
  available_ea: number
  /** In Radiant's report, holding stock, or moved after it; a closed lot counts in no total. */
  open: boolean
  kind: CompareKind
}

export const COMPARE_KIND: Record<CompareKind, string> = {
  match: 'Matches', differs: 'Differs', sales_only: 'Only in Sales', radiant_only: 'Only at Radiant', no_lot: 'Shipped with no lot',
}

export const stockCountsClient = createResourceClient<StockCountRead, never>(api, '/inventory/counts')
export function uploadCount(file: File, countedAt: string, note: string | null) {
  const form = new FormData()
  form.append('file', file)
  form.append('counted_at', countedAt)
  if (note) form.append('note', note)
  return api.upload<StockCountRead>('/inventory/counts', form)
}
export const fetchCount = (id: number) => api.get<StockCountRead>(`/inventory/counts/${id}`)
export const fetchCountLines = (id: number) => api.get<StockCountLineRead[]>(`/inventory/counts/${id}/lines`)
export const fetchCountCompare = (id: number) => api.get<CountCompareRow[]>(`/inventory/counts/${id}/compare`)
export const updateCount = (id: number, version: number, countedAt: string | null, note: string | null) =>
  api.put<StockCountRead>(`/inventory/counts/${id}`, { version, counted_at: countedAt, note })
export const applyCount = (id: number, version: number) => api.post<StockCountRead>(`/inventory/counts/${id}/apply`, { version })
export const undoCount = (id: number, version: number, reason: string) =>
  api.post<StockCountRead>(`/inventory/counts/${id}/undo`, { version, reason })
