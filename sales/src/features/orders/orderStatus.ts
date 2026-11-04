import { formatDate, formatDateTime } from '@/platform-ui'
import type { OrderRead } from './ordersApi'

type Tone = 'neutral' | 'info' | 'success' | 'warning'

// The one place the Status gets its colour; the values come from sales-api (app/domain/order_status.py).
const STATUS_TONE: Record<string, Tone> = {
  Draft: 'neutral', Cancelled: 'neutral', 'On Hold': 'warning', Completed: 'success',
  'With Zoho (shadow)': 'neutral', 'Waiting for Delivery (shadow)': 'neutral', 'Completed (shadow)': 'neutral',
}
export const orderStatusTone = (status: string): Tone => STATUS_TONE[status] ?? 'info'

/** "Sends at Nov 4, 2026, 3:00 p.m.": the message and the time it names, in the viewer's format. */
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
