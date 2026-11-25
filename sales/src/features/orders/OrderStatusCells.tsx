import {
  Badge, CountdownCell, HelpTip, Hint, RelativeCell, StatusCell, TourMark, dateAndTime, Cluster, Text,
} from '@/platform-ui'
import type { OrderRead } from './ordersApi'
import { orderStatusColour, shopifyBadges, statusDetailText } from './orderStatus'

/** The Status, and beside it its marks (never a status of their own): Short shipped when the 945s left something unshipped,
 *  "2 shipments" from two 945s that count, Over shipped with the EA above what the 940 carried.
 *  Each is a button: a tap says what it means (the API's status help; an On Hold order: its reason's). */
export function OrderStatusBadge({ o }: { o: Pick<OrderRead, 'order_status' | 'order_status_label' | 'short_shipped' | 'applied_945s' | 'over_shipped_ea' | 'on_hold_reason'> }) {
  return (
    <Cluster as="span" inline gap={1} align="center">
      <HelpTip id={o.on_hold_reason ? `hold:${o.on_hold_reason}` : `status:${o.order_status}`} tourSlot="row.status" tourKey="status" label={`What "${o.order_status_label}" means`}>
        <StatusCell value={o.order_status_label} className={orderStatusColour(o.order_status)} />
      </HelpTip>
      {o.short_shipped && (
        <HelpTip id="detail:Short shipped" label='What "Short shipped" means'><Badge variant="warning">Short shipped</Badge></HelpTip>
      )}
      {(o.applied_945s ?? 0) > 1 && (
        <HelpTip id="detail:Shipments" label='What "shipments" counts'><Badge variant="info">{o.applied_945s} shipments</Badge></HelpTip>
      )}
      {(o.over_shipped_ea ?? 0) > 0 && (
        <HelpTip id="detail:Over shipped" label='What "Over shipped" means'><Badge variant="warning">Over shipped {o.over_shipped_ea} EA</Badge></HelpTip>
      )}
    </Cluster>
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
      <Text weight="medium" className={`rounded-full border px-1.5 ${turn.className}`}>{turn.label}</Text>
      <Text tone="secondary">{o.next_step}</Text>
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
      <Text tone="body" wrap="clamp2">{o.status_detail}{at ? ` ${day}` : ''}</Text>
      {at && (
        <Cluster as="span" gap={0} align="stretch" size="xs" tone="muted" className="gap-x-1 whitespace-nowrap">
          {time && <span>{time} ·</span>}
          {o.status_detail_kind === 'countdown' ? <CountdownCell value={at} className="text-xs" /> : <RelativeCell value={at} />}
        </Cluster>
      )}
    </span>
  )
}

/** The list's Shopify column: a dash for any other order. */
export function ShopifyCell({ o }: { o: Parameters<typeof shopifyBadges>[0] }) {
  if (o.source_type !== 'SHOPIFY') return <Hint>—</Hint>
  return (
    <Cluster as="span" inline gap={1} align="center">
      {shopifyBadges(o).map((b) => <Badge key={b.label} variant={b.tone}>{b.label}</Badge>)}
    </Cluster>
  )
}
