import {
  ActionButton, formatTime, queryKeys, type ActionDone, Stack, Text,
} from '@/platform-ui'
import { ordersClient, resolveReview, type OrderRead } from './ordersApi'

/** Re-check a Review order against today's Item Master (FR-OB-15;
 *  sales.order.operate). Always says that the check ran — in the list's or the
 *  sheet's status row: a reason that is still true reads the same as before.
 *  `compact`: the list's row action; `bare`: the button only (a sheet's action bar). */
export function Recheck({ o, compact, bare, onResolved }: { o: OrderRead; compact?: boolean; bare?: boolean; onResolved?: () => void }) {
  const ref = o.source_reference ?? `#${o.order_number}`
  // Always said: still a problem (a warning, the reasons) or resolved; the order and the list read again.
  const said = (r: unknown): ActionDone => {
    const remaining = (r as Awaited<ReturnType<typeof resolveReview>>).remaining
    const at = formatTime(Date.now())
    return remaining.length > 0
      ? { key: `recheck:${o.id}`, tone: 'warning', text: `${ref} re-checked at ${at}: still ${remaining.length === 1 ? '1 problem' : `${remaining.length} problems`} — ${remaining.join('; ')}` }
      : { key: `recheck:${o.id}`, tone: 'success', text: `${ref} re-checked at ${at}: resolved` }
  }
  const button = (
    <ActionButton size="sm" variant={compact ? 'secondary' : undefined} permission="sales.order.operate"
      done={said} invalidates={[queryKeys.list(ordersClient), queryKeys.detail(ordersClient, o.id)]}
      after={(r) => { if ((r as Awaited<ReturnType<typeof resolveReview>>).remaining.length === 0) onResolved?.() }}
      onAction={() => resolveReview(o.id)}>Re-check</ActionButton>
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
