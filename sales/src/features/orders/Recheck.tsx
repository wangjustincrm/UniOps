import { orderMoved } from '@/features/common/moved'
import {
  ActionButton, formatTime, type ActionDone, type ActionResultRow, Stack, Text,
} from '@/platform-ui'
import { resolveReview, type OrderRead } from './ordersApi'

const errorMessages: Record<string, string> = {
  'entity.stale': 'The order changed since you opened it: it is read again here. Look, then re-check again.',
  'auth.forbidden': 'You do not have permission to do this.',
}

/** Re-check a Review order against today's Item Master (FR-OB-15;
 *  sales.order.operate). Always says that the check ran — in the list's or the
 *  sheet's status row: a reason that is still true reads the same as before.
 *  `compact`: the list's row action; `bare`: the button only (a sheet's action bar). */
export function Recheck({ o, compact, bare, onResolved }: { o: OrderRead; compact?: boolean; bare?: boolean; onResolved?: () => void }) {
  const ref = o.source_reference ?? `#${o.order_number}`
  // Always said: still a problem (a warning, the reasons) or resolved; the order and the list read again.
  const said = (r: unknown): ActionDone => {
    const note = (r as ActionResultRow).note
    const at = formatTime(Date.now())
    return note
      ? { key: `recheck:${o.id}`, tone: 'warning', text: `${ref} re-checked at ${at}: ${note}` }
      : { key: `recheck:${o.id}`, tone: 'success', text: `${ref} re-checked at ${at}: resolved` }
  }
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
