import type { ReactNode } from 'react'
import { formatDate, formatMoney, type RecordFact } from '@/platform-ui'
import type { OrderRead } from './orderTypes'

/** What the mark says, on the order's facts: by hand (the date, reference, amount, who, note), or auto (the gateway
 *  settlement, `link` opens it). */
export function settledFacts(o: OrderRead, link?: ReactNode): RecordFact[] {
  if (!o.settled_on && o.auto_settlement_id != null) {
    return [{ label: 'Settled (auto)', value: o.auto_settled_on ? formatDate(o.auto_settled_on) : '—', tone: 'success',
      help: 'term:Payment', hint: link ?? o.auto_settlement_label }]
  }
  if (!o.settled_on) return []
  return [{ label: 'Settled', value: formatDate(o.settled_on), tone: 'success', help: 'term:Payment',
    hint: [o.settled_reference, o.settled_amount != null ? formatMoney(o.settled_amount, o.currency) : null,
      o.settled_by_name ? `by ${o.settled_by_name}` : null, o.settled_note].filter(Boolean).join(' · ') }]
}
