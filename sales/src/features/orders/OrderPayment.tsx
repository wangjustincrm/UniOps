import { Badge, Button, HelpTip, useOpenRecord } from '@/platform-ui'
import type { OrderRead } from './ordersApi'

// Payment (user 2026-11-23): beside the Status, whether the money came. Settled is a finance user's mark
// (sales.settle): Mark settled / Undo settled are record actions (orderActions.ts).
const VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  Settled: 'success', Due: 'warning', Overdue: 'danger', 'Not settled': 'neutral', 'Not needed': 'neutral',
  'Waiting for the gateway': 'neutral',
}
const isAuto = (o: Pick<OrderRead, 'payment_status' | 'settled_on' | 'auto_settlement_id'>) =>
  o.payment_status === 'Settled' && !o.settled_on && o.auto_settlement_id != null
/** The Payment badge; a tap says what it means (the API's payment help). */
export function PaymentBadge({ o }: { o: Pick<OrderRead, 'payment_status' | 'settled_on' | 'auto_settlement_id'> }) {
  if (!o.payment_status) return null
  return (
    <HelpTip id={`payment:${o.payment_status}`} label={`What "${o.payment_status}" means`}>
      <Badge variant={VARIANT[o.payment_status] ?? 'neutral'}>{o.payment_status}{isAuto(o) ? ' (auto)' : ''}</Badge>
    </HelpTip>
  )
}

/** The settlement that settled a Shopify order: opens it in Settlements. */
export function SettlementLink({ o }: { o: Pick<OrderRead, 'auto_settlement_id' | 'auto_settlement_label'> }) {
  const openRecord = useOpenRecord()
  if (o.auto_settlement_id == null) return null
  return (
    <Button size="sm" variant="ghost" className="h-auto max-w-full px-0 text-left text-primary-700 underline-offset-2 hover:underline"
      onClick={() => { openRecord('/settlements', o.auto_settlement_id!) }}>
      {o.auto_settlement_label}
    </Button>
  )
}
