import { Badge, CountdownCell, Hint, RelativeCell, StatusCell, Tooltip, dateAndTime } from '@/platform-ui'
import type { OrderRead } from './ordersApi'
import { orderStatusColour, shopifyBadges, statusDetailText } from './orderStatus'

/** The Status, and beside it Short shipped when the 945 left something unshipped: a mark, not a status. */
export function OrderStatusBadge({ o }: { o: Pick<OrderRead, 'order_status' | 'order_status_label' | 'short_shipped'> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusCell value={o.order_status_label} className={orderStatusColour(o.order_status)} />
      {o.short_shipped && <Tooltip tip="The warehouse shipped less than ordered; the rest is not sent later (one 945 per order)."><span><Badge variant="warning">Short shipped</Badge></span></Tooltip>}
    </span>
  )
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
