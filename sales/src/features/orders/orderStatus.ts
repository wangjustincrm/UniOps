import { formatDate, formatDateTime } from '@/platform-ui'
import type { Later945, OrderRead, ShipmentRead } from './ordersApi'

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

/** What adding the second 945 does, in words: the two are different decisions (Compare the 945s). */
export function laterChoice(x: Later945): { label: string; text: string; confirm: string } | null {
  if (x.kind === 'part') {
    const what = x.items.map((i) => `${i.item_code}: ${i.this_ea} EA`).join('; ')
    return { label: 'Apply the missing part', text: `It brings what the applied 945 left (${what}). The quantities are added up and the order goes back to Shipped.`,
      confirm: `${what} is added to what the applied 945 shipped, and the order goes back to Shipped. Nothing is written to Shopify for it and nobody is emailed about it.` }
  }
  if (x.kind === 'more') {
    const above = x.items.filter((i) => i.above_ea > 0)
      .map((i) => `${i.item_code}: ${i.above_ea} EA above the ${i.carried_ea} EA on the 940 (${i.earlier_ea + i.this_ea} EA in all)`).join('; ')
    return { label: 'Apply, more than ordered', text: `It is more than the 940 carried. ${above}.`,
      confirm: `${above}. Only for goods that left beside the applied 945: a corrected 945 of the same goods (another lot or date) must not be applied, the stock `
        + 'would be taken twice. The order goes back to Shipped. Nothing is written to Shopify for it and nobody is emailed about it.' }
  }
  return null
}

/** The 945s that count, as one: what the second is compared with (two once a later part was added). */
export function applied945(shipments: ShipmentRead[]): ShipmentRead | null {
  const all = shipments.filter((s) => s.applied)
  if (all.length < 2) return all[0] ?? null
  return { ...all[0], ship_date: [...new Set(all.map((s) => s.ship_date ?? '—'))].join(', '), tracking: all.flatMap((s) => s.tracking ?? []), rows: all.flatMap((s) => s.rows) }
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
