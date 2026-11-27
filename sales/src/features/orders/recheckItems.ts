import { orderMoved } from '@/features/common/moved'
import { formatTime, type ActionDone, type ActionResultRow, type BarItem } from '@/platform-ui'
import { resolveReview, type OrderRead } from './ordersApi'

export const recheckErrors: Record<string, string> = {
  'entity.stale': 'The order changed since you opened it: it is read again here. Look, then re-check again.',
  'auth.forbidden': 'You do not have permission to do this.',
}

/** Always said: still a problem (a warning, the reasons) or resolved; the order and the list read again. */
export function recheckSaid(o: OrderRead) {
  const ref = o.source_reference ?? `#${o.order_number}`
  return (r: unknown): ActionDone => {
    const note = (r as ActionResultRow).note
    const at = formatTime(Date.now())
    return note
      ? { key: `recheck:${o.id}`, tone: 'warning', text: `${ref} re-checked at ${at}: ${note}` }
      : { key: `recheck:${o.id}`, tone: 'success', text: `${ref} re-checked at ${at}: resolved` }
  }
}

/** Re-check in the order's action bar (a Review order): a button. */
export function recheckItems(o: OrderRead, onResolved?: () => void): BarItem[] {
  if (o.fulfillment_status !== 'Review') return []
  return [{
    id: 'recheck', label: 'Re-check', permission: 'sales.order.operate', primary: 8, errorMessages: recheckErrors, done: recheckSaid(o), invalidates: orderMoved(o.id),
    hint: 'Fix the cause in the Item Master (an item, a unit, a partner code, a DC route), then re-check',
    after: (r) => { if (!(r as ActionResultRow).note) onResolved?.() },
    onSelect: () => resolveReview(o.id, o.version),
  }]
}
