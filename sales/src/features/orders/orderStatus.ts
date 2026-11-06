import { formatDate, formatDateTime } from '@/platform-ui'
import type { OrderRead, ShipmentRead } from './ordersApi'

type Tone = 'neutral' | 'info' | 'success' | 'warning'

// The one place the Status gets its colour; the values come from sales-api (app/domain/order_status.py).
// Each stage has its own colour, so where an order is reads at a glance; the name is always written too.
const STATUS_COLOUR: Record<string, string> = {
  Draft: 'bg-neutral-100 text-neutral-600',
  'To Fulfill': 'bg-stage-fulfill text-stage-fulfill-fg',
  Sent: 'bg-stage-sent text-stage-sent-fg',
  Fulfilling: 'bg-stage-fulfilling text-stage-fulfilling-fg',
  Shipped: 'bg-stage-shipped text-stage-shipped-fg',
  'To Invoice': 'bg-stage-invoice text-stage-invoice-fg',
  Completed: 'bg-stage-done text-stage-done-fg',
  'On Hold': 'bg-stage-hold text-stage-hold-fg',
  Cancelled: 'bg-neutral-200 text-neutral-500',
}
export const orderStatusColour = (status: string): string => STATUS_COLOUR[status] ?? 'bg-neutral-100 text-neutral-600'

/** "Sends Nov 4, 2026, 3:00 p.m.": the message and the time it names, in the viewer's format. */
export function statusDetailText(o: Pick<OrderRead, 'status_detail' | 'status_detail_at'>): string {
  if (!o.status_detail) return ''
  const at = o.status_detail_at
  return at ? `${o.status_detail} ${at.length === 10 ? formatDate(at) : formatDateTime(at)}` : o.status_detail
}

type ShopifyFacts = Pick<OrderRead, 'source_type' | 'shopify_financial_status' | 'shopify_fulfillment_status' | 'archived_in_shopify_at' | 'source_cancelled_at'>

/** Shopify's facts as badges: only what has a value. */
export function shopifyBadges(o: ShopifyFacts): { label: string; tone: Tone }[] {
  const pay = o.shopify_financial_status
  const words = (s: string) => { const t = s.replace(/_/g, ' '); return t[0].toUpperCase() + t.slice(1) }
  return [
    ...(pay ? [{ label: words(pay), tone: (pay === 'paid' ? 'success' : pay.includes('refunded') ? 'warning' : 'neutral') as Tone }] : []),
    ...(o.shopify_fulfillment_status ? [{ label: words(o.shopify_fulfillment_status), tone: 'neutral' as Tone }] : []),
    ...(o.archived_in_shopify_at ? [{ label: 'Archived', tone: 'neutral' as Tone }] : []),
    ...(o.source_cancelled_at ? [{ label: 'Cancelled', tone: 'warning' as Tone }] : []),
  ]
}

/** The two 945s of a conflict, side by side: what the applied one says against the second. */
export function compare945(a: ShipmentRead, b: ShipmentRead): { left: string | null; right: string | null; same: boolean }[] {
  const row = (label: string, x: string, y: string) => ({ left: `${label}: ${x}`, right: `${label}: ${y}`, same: x === y })
  const qty = (s: ShipmentRead, item: string) => s.rows.filter((r) => r.item_code === item).reduce((n, r) => n + r.qty_shipped_ea, 0)
  const lots = (s: ShipmentRead, item: string) => [...new Set(s.rows.filter((r) => r.item_code === item).map((r) => r.lot ?? '—'))].sort().join(', ') || '—'
  const items = [...new Set([...a.rows, ...b.rows].map((r) => r.item_code ?? '?'))].sort()
  return [
    row('Ship date', a.ship_date ?? '—', b.ship_date ?? '—'),
    ...items.flatMap((i) => [row(`${i} EA`, String(qty(a, i)), String(qty(b, i))), row(`${i} lots`, lots(a, i), lots(b, i))]),
    row('Tracking', (a.tracking ?? []).join(', ') || '—', (b.tracking ?? []).join(', ') || '—'),
  ]
}
