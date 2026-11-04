import { Badge, Hint, StatusCell } from '@/platform-ui'
import type { OrderRead } from './ordersApi'
import { orderStatusTone, shopifyBadges, statusDetailText } from './orderStatus'

/** The Status, and beside it Short shipped when the 945 left something unshipped: a mark, not a status. */
export function OrderStatusBadge({ o }: { o: Pick<OrderRead, 'order_status' | 'order_status_label' | 'short_shipped'> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusCell value={o.order_status_label} tone={orderStatusTone(o.order_status)} />
      {o.short_shipped && <Badge variant="warning">Short shipped</Badge>}
    </span>
  )
}

export function StatusDetail({ o }: { o: Pick<OrderRead, 'status_detail' | 'status_detail_at'> }) {
  const text = statusDetailText(o)
  return text ? <Hint className="line-clamp-2 block min-w-44 max-w-xs">{text}</Hint> : null
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
