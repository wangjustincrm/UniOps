import { Badge, CountdownCell, HelpTip, Hint, RelativeCell, StatusCell, dateAndTime, useHelp } from '@/platform-ui'
import type { OrderRead } from './ordersApi'
import { orderStatusColour, shopifyBadges, statusDetailText } from './orderStatus'

/** The Status, and beside it Short shipped when the 945 left something unshipped: a mark, not a status.
 *  Each is a button: a tap says what it means (the API's status help; an On Hold order: its reason's). */
export function OrderStatusBadge({ o }: { o: Pick<OrderRead, 'order_status' | 'order_status_label' | 'short_shipped' | 'on_hold_reason'> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <HelpTip id={o.on_hold_reason ? `hold:${o.on_hold_reason}` : `status:${o.order_status}`} label={`What "${o.order_status_label}" means`}>
        <StatusCell value={o.order_status_label} className={orderStatusColour(o.order_status)} />
      </HelpTip>
      {o.short_shipped && (
        <HelpTip id="detail:Short shipped" label='What "Short shipped" means'><Badge variant="warning">Short shipped</Badge></HelpTip>
      )}
    </span>
  )
}

/** What to do about the order now, in one line under its status (the record sheet): its On Hold
 *  reason's text, else its Status's (the same text the badge opens). */
export function StatusGuide({ o }: { o: Pick<OrderRead, 'order_status' | 'on_hold_reason'> }) {
  const help = useHelp(o.on_hold_reason ? `hold:${o.on_hold_reason}` : `status:${o.order_status}`)
  return help ? <Hint className="max-w-prose">{help.body}</Hint> : null
}

/** The Detail's countdown words (Sends, Delivery due) have a ? that says what the date is. */
export function DetailHelp({ o }: { o: Pick<OrderRead, 'status_detail' | 'status_detail_kind'> }) {
  return o.status_detail && o.status_detail_kind === 'countdown' ? <HelpTip id={`detail:${o.status_detail}`} /> : null
}

/** What the Status does not say, in two short lines: the fact and its day, then the time and how far
 *  away it is. A time still to come turns red once it passes. */
export function StatusDetail({ o }: { o: Pick<OrderRead, 'status_detail' | 'status_detail_at' | 'status_detail_kind'> }) {
  const text = statusDetailText(o)
  if (!text) return null
  const at = o.status_detail_at
  const { day, time } = dateAndTime(at)
  return (
    <span className="block max-w-56 leading-tight" title={text}>
      <span className="line-clamp-2 text-neutral-800">{o.status_detail}{at ? ` ${day}` : ''}</span>
      {at && (
        <span className="flex flex-wrap gap-x-1 whitespace-nowrap text-xs text-neutral-500">
          {time && <span>{time} ·</span>}
          {o.status_detail_kind === 'countdown' ? <CountdownCell value={at} className="text-xs" /> : <RelativeCell value={at} />}
        </span>
      )}
    </span>
  )
}

/** The list's Shopify column: a dash for any other order. */
export function ShopifyCell({ o }: { o: Parameters<typeof shopifyBadges>[0] }) {
  if (o.source_type !== 'SHOPIFY') return <Hint>—</Hint>
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {shopifyBadges(o).map((b) => <Badge key={b.label} variant={b.tone}>{b.label}</Badge>)}
    </span>
  )
}
