import { orderMoved } from '@/features/common/moved'
import { ActionButton, type ActionResultRow, Stack, Text } from '@/platform-ui'
import { resolveReview, type OrderRead } from './ordersApi'
import { recheckErrors as errorMessages, recheckSaid } from './recheckItems'

/** Re-check a Review order against today's Item Master (FR-OB-15;
 *  sales.order.operate). Always says that the check ran — in the list's or the
 *  sheet's status row: a reason that is still true reads the same as before.
 *  `compact`: the list's row action; `bare`: the button only (a sheet's action bar). */
export function Recheck({ o, compact, bare, onResolved }: { o: OrderRead; compact?: boolean; bare?: boolean; onResolved?: () => void }) {
  const said = recheckSaid(o)
  const button = (
    <ActionButton size="sm" variant={compact ? 'secondary' : undefined} permission="sales.order.operate"
      done={said} invalidates={orderMoved(o.id)}
      after={(r) => { if (!(r as ActionResultRow).note) onResolved?.() }}
      errorMessages={errorMessages} onAction={() => resolveReview(o.id, o.version)}>Re-check</ActionButton>
  )
  if (bare) return button
  if (compact) return button  // in a row: the table never opens the order for a click on a button
  return (
    <Stack aria-label="Resolve" as="section" gap={2} size="sm">
      <Text as="p" tone="soft">Fix the cause in the Item Master (an item, a unit, a partner code, a DC route), then re-check.</Text>
      {button}
    </Stack>
  )
}
