import { formatDate, formatMoney, type RecordFact } from '@/platform-ui'
import type { OrderRead } from './orderTypes'

/** What the mark says, on the order's facts. */
export function settledFacts(o: OrderRead): RecordFact[] {
  if (!o.settled_on) return []
  return [{ label: 'Settled', value: formatDate(o.settled_on), tone: 'success', help: 'term:Payment',
    hint: [o.settled_reference, o.settled_amount != null ? formatMoney(o.settled_amount, o.currency) : null,
      o.settled_by_name ? `by ${o.settled_by_name}` : null, o.settled_note].filter(Boolean).join(' · ') }]
}
