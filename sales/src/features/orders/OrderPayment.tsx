import { Badge, HelpTip } from '@/platform-ui'
import type { OrderRead } from './ordersApi'

// Payment (user 2026-11-23): beside the Status, whether the money came. Settled is a finance user's mark
// (sales.settle): Mark settled / Undo settled are record actions (orderActions.ts).
const VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  Settled: 'success', Due: 'warning', Overdue: 'danger', 'Not settled': 'neutral', 'Not needed': 'neutral',
}
/** The Payment badge; a tap says what it means (the API's payment help). */
export function PaymentBadge({ o }: { o: Pick<OrderRead, 'payment_status'> }) {
  if (!o.payment_status) return null
  return (
    <HelpTip id={`payment:${o.payment_status}`} label={`What "${o.payment_status}" means`}>
      <Badge variant={VARIANT[o.payment_status] ?? 'neutral'}>{o.payment_status}</Badge>
    </HelpTip>
  )
}
