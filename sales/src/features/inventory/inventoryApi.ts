import { api } from '@/lib/api'
import { createResourceClient, type QuerySpec } from '@/platform-ui'

/** One item at Radiant (design §15): every figure is a sum of its moves. */
export interface StockItemRead {
  id: number
  item_code: string
  description: string | null
  on_hand_ea: number
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
  on_hand_ea: number
  received_ea: number
  shipped_ea: number
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
  transfer_line: 'Transfer sent (943)', receipt_row: 'Received (944)', return_944: 'Return received (RA 944)', return_order: 'Returned by customer (RA)',
  shipment_row: 'Shipped (945)', order_line: 'Shipped (order, no 945)', delivery: 'Delivered', returned: 'Returned by customer',
  adjustment: 'Adjustment',
}
