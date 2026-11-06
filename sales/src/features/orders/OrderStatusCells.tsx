import { Badge, CountdownCell, Hint, RelativeCell, StatusCell } from '@/platform-ui'
import type { OrderRead } from './ordersApi'
import { orderStatusColour, shopifyBadges, statusDetailText } from './orderStatus'

/** The Status, and beside it Short shipped when the 945 left something unshipped: a mark, not a status. */
export function OrderStatusBadge({ o }: { o: Pick<OrderRead, 'order_status' | 'order_status_label' | 'short_shipped'> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusCell value={o.order_status_label} className={orderStatusColour(o.order_status)} />
      {o.short_shipped && <Badge variant="warning">Short shipped</Badge>}
    </span>
  )
}

/** What the Status does not say: the fact, and how far away its time is. A time still to come turns red once it passes. */
export function StatusDetail({ o }: { o: Pick<OrderRead, 'status_detail' | 'status_detail_at' | 'status_detail_kind'> }) {
  const text = statusDetailText(o)
  if (!text) return null
  return (
    <span className="block min-w-44 max-w-xs text-sm text-neutral-700">
      <span className="line-clamp-2" title={text}>{text}</span>
      {o.status_detail_at && (o.status_detail_kind === 'countdown'
        ? <CountdownCell value={o.status_detail_at} className="text-xs" />
        : <span className="text-xs text-neutral-500"><RelativeCell value={o.status_detail_at} /></span>)}
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
