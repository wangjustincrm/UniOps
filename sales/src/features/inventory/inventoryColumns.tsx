import {
  columnsOf, startHidden, withHelp, type ColumnDef, Text,
} from '@/platform-ui'
import type { StockItemRead, StockLotRead } from './inventoryApi'

const colItem = columnsOf<StockItemRead>()
const colLot = columnsOf<StockLotRead>()

// The figures (user 2026-11-13/16), read left to right: received − shipped (net: a customer's return comes off it) = on
// hand; on hand − in process − on hold = available. Each row balances. In process: an item's 940s sent with no 945 yet; a
// lot's, what Radiant's latest report shows picked for those orders (a 940 names no lot). The difference with Radiant's
// history is on the Radiant reports page only; the rest is in the column menu.
export const itemColumns: ColumnDef<StockItemRead, unknown>[] = [
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => <Text weight="medium">{row.original.item_code}</Text> },
  colItem.text('description', 'Description'),
  colItem.number('received_ea', 'Received'),
  colItem.number('shipped_ea', 'Shipped'),
  withHelp(colItem.number('on_hand_ea', 'On hand'), 'term:On hand'),
  withHelp(colItem.number('allocated_ea', 'In process'), 'term:In process'),
  withHelp(colItem.number('hold_ea', 'On hold'), 'term:On hold'),
  withHelp(colItem.number('available_ea', 'Available'), 'term:Available'),
  startHidden(colItem.number('returned_ea', 'Returned (in Shipped)')),
  startHidden(colItem.number('adjusted_ea', 'Adjusted')),
  colItem.date('first_expiry', 'First expiry'),
  startHidden(colItem.number('lots', 'Lots on hand')),
  startHidden(withHelp(colItem.number('to_radiant_ea', 'In transit to Radiant'), 'term:In transit to Radiant')),
  startHidden(withHelp(colItem.number('to_customer_ea', 'In transit to customers'), 'term:In transit to customers')),
  startHidden(withHelp(colItem.number('returning_ea', 'Returns on the way'), 'term:Returns on the way')),
  startHidden(colItem.datetime('last_move_at', 'Last move')),
]

export const lotColumns: ColumnDef<StockLotRead, unknown>[] = [
  colLot.text('item_code', 'Item'),
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => <Text weight="medium">{row.original.lot ?? '(no lot)'}</Text> },
  colLot.date('expiry', 'Expiry'),
  colLot.number('days_to_expiry', 'Days to expiry'),
  colLot.number('received_ea', 'Received'),
  colLot.number('shipped_ea', 'Shipped'),
  withHelp(colLot.number('on_hand_ea', 'On hand'), 'term:On hand'),
  withHelp(colLot.number('in_process_ea', 'In process'), 'term:In process'),
  withHelp(colLot.number('hold_ea', 'On hold'), 'term:On hold'),
  withHelp(colLot.number('available_ea', 'Available'), 'term:Available'),
  startHidden(colLot.number('returned_ea', 'Returned (in Shipped)')),
  startHidden(colLot.number('adjusted_ea', 'Adjusted')),
  startHidden(withHelp(colLot.number('to_customer_ea', 'In transit to customers'), 'term:In transit to customers')),
  startHidden(withHelp(colLot.number('returning_ea', 'Returns on the way'), 'term:Returns on the way')),
  startHidden(colLot.datetime('last_move_at', 'Last move')),
]
