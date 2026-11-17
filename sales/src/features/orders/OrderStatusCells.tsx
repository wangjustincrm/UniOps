import { Badge, CountdownCell, HelpTip, Hint, RelativeCell, StatusCell, TourMark, dateAndTime } from '@/platform-ui'
import type { OrderRead } from './ordersApi'
import { orderStatusColour, shopifyBadges, statusDetailText } from './orderStatus'

/** The Status, and beside it Short shipped when the 945 left something unshipped: a mark, not a status.
 *  Each is a button: a tap says what it means (the API's status help; an On Hold order: its reason's). */
export function OrderStatusBadge({ o }: { o: Pick<OrderRead, 'order_status' | 'order_status_label' | 'short_shipped' | 'on_hold_reason'> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <HelpTip id={o.on_hold_reason ? `hold:${o.on_hold_reason}` : `status:${o.order_status}`} tourSlot="row.status" tourKey="status" label={`What "${o.order_status_label}" means`}>
        <StatusCell value={o.order_status_label} className={orderStatusColour(o.order_status)} />
      </HelpTip>
      {o.short_shipped && (
        <HelpTip id="detail:Short shipped" label='What "Short shipped" means'><Badge variant="warning">Short shipped</Badge></HelpTip>
      )}
    </span>
  )
}

const TURN: Record<string, { label: string; className: string }> = {
  you: { label: 'Your turn', className: 'border-primary-200 bg-primary-50 text-primary-800' },
  warehouse: { label: 'Waiting on warehouse', className: 'border-neutral-200 bg-neutral-50 text-neutral-700' },
  shopify: { label: 'Waiting on Shopify', className: 'border-neutral-200 bg-neutral-50 text-neutral-700' },
  nobody: { label: 'Nothing to do', className: 'border-neutral-200 bg-neutral-50 text-neutral-600' },
}

/** What to do about this order now and whose turn it is, under its status (the record sheet): built
 *  by the server from the order's own facts (the user's approved table, 2026-11-16). */
export function NextStep({ o }: { o: Pick<OrderRead, 'next_step' | 'next_step_who'> }) {
  if (!o.next_step) return null
  const turn = TURN[o.next_step_who ?? 'nobody'] ?? TURN.nobody
  return (
    <TourMark help="record.next" slot="record.next" className="inline-flex max-w-prose flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs">
      <span className={`rounded-full border px-1.5 font-medium ${turn.className}`}>{turn.label}</span>
      <span className="text-neutral-700">{o.next_step}</span>
    </TourMark>
  )
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
