import { AddressView, Badge, Callout, DetailList, Section, formatDate, formatDateTime, formatMoney } from '@/platform-ui'
import { partnerLabel } from '@/features/common/labels'
import type { OrderDetail } from '../ordersApi'

// Where each address came from, under it (the 850 ship-to also names its GLN and the DC it routes to).
function shipToOrigin(o: OrderDetail): string {
  if (o.source_type === 'SHOPIFY') return 'From the Shopify shipping address'
  if (o.source_type === 'MANUAL') return 'Entered by hand'
  const gln = o.ship_to?.gln
  return ['From the 850 ShipTo', gln && `GLN ${gln}`, o.dc_number && `DC ${o.dc_number}`].filter(Boolean).join(' · ')
}
function billToOrigin(o: OrderDetail): string {
  if (o.source_type === 'SHOPIFY') return 'From the Shopify billing address (the shipping address when missing or outside Canada)'
  if (o.source_type === 'MANUAL') return 'None for a manual order'
  return ['From the 850 BillTo', o.bill_to?.code && `code ${o.bill_to.code}`].filter(Boolean).join(' · ')
}

/** Why it is in review, the order's facts, and its addresses. */
export function OverviewTab({ o }: { o: OrderDetail }) {
  return (
    <>
      {o.review_reason && (
        <Callout label="Review reason" title="Needs review">
          <pre className="whitespace-pre-wrap text-xs">{o.review_reason}</pre>
          <p className="text-xs">Fix the cause in the Item Master (an item, a unit, a partner code, a DC route), then Re-check.</p>
        </Callout>
      )}
      <Section title="Order">
        <DetailList items={[
          ['Order ref', o.source_reference], ['Internal no.', String(o.order_number)],
          ['Partner', partnerLabel(o.partner_name, o.partner_code)], ['Customer PO / reference', o.source_order_number],
          ['Source', o.source_type], ['Received', formatDateTime(o.first_received_at)], ['Paid (delay start)', formatDateTime(o.paid_at)],
          ['Requested delivery', o.requested_delivery_date ? formatDate(o.requested_delivery_date) : null],
          ['Total', formatMoney(o.total_amount ?? o.total, o.currency)], ['Invoice', o.invoice_status],
          ['Notes', o.notes],
        ]} />
      </Section>
      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Ship to">
          <AddressView value={o.ship_to} note={shipToOrigin(o)} />
          {(o.ship_to_flags ?? []).map((f) => <Badge key={f} variant="warning">{f}</Badge>)}
        </Section>
        <Section title="Bill to"><AddressView value={o.bill_to} note={billToOrigin(o)} /></Section>
      </div>
    </>
  )
}
