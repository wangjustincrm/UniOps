import { AddressView, Badge, Callout, DetailList, Hint, Section, formatDate, formatDateTime, formatMoney } from '@/platform-ui'
import { partnerLabel } from '@/features/common/labels'
import { SOURCE_LABEL, type OrderDetail } from '../ordersApi'

// Where each address came from, under it (the 850 ship-to also names its GLN and the DC it routes to).
function shipToOrigin(o: OrderDetail): string {
  if (o.source_type === 'SHOPIFY') return 'From the Shopify shipping address'
  if (o.source_type === 'MANUAL') return 'Entered by hand'
  if (o.source_type === 'FROM_945') return 'From the 945 ship-to'
  const gln = o.ship_to?.gln
  return ['From the 850 ShipTo', gln && `GLN ${gln}`, o.dc_number && `DC ${o.dc_number}`].filter(Boolean).join(' · ')
}
function billToOrigin(o: OrderDetail): string {
  if (o.source_type === 'SHOPIFY') return 'From the Shopify billing address (the shipping address when missing or outside Canada)'
  if (o.source_type === 'MANUAL') return 'None for a manual order'
  if (o.source_type === 'FROM_945') return 'None: a 945 has no bill-to'
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
          ['Source', SOURCE_LABEL[o.source_type] ?? o.source_type], ['Received', formatDateTime(o.first_received_at)], ['Paid (delay start)', formatDateTime(o.paid_at)],
          ['Requested delivery', o.requested_delivery_date ? formatDate(o.requested_delivery_date) : null],
          ...(o.source_type === 'EDI_850' ? [['EDI mailbox', o.edi_mailbox] as [string, string | null | undefined]] : []),
          ['Total', formatMoney(o.total_amount ?? o.total, o.currency)],
          ['Notes', o.notes],
        ]} />
      </Section>
      {o.source_type === 'SHOPIFY' && (
        <Section title="Shopify">
          <Hint as="p">As Shopify reports them; read only.</Hint>
          <DetailList items={[
            ['Payment', o.shopify_financial_status], ['Fulfillment', o.shopify_fulfillment_status],
            ['Archived', o.archived_in_shopify_at ? formatDateTime(o.archived_in_shopify_at) : 'No'],
            ['Cancelled', o.source_cancelled_at ? formatDateTime(o.source_cancelled_at) : 'No'],
          ]} />
        </Section>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Ship to">
          <AddressView value={o.ship_to} note={shipToOrigin(o)} />
          {(o.ship_to_flags ?? []).map((f) => <Badge key={f} variant="warning">{f}</Badge>)}
        </Section>
        <Section title="Bill to"><AddressView value={o.bill_to} note={billToOrigin(o)} /></Section>
      </div>
      <Section title="Internal status">
        <Hint as="p">The raw values, for diagnosis. The statuses at the top are worked out from these.</Hint>
        <DetailList items={[
          ['Fulfillment', o.fulfillment_status], ['Invoice', o.invoice_status], ['Order state', o.order_state], ['Hold reason', o.hold_reason],
        ]} />
      </Section>
    </>
  )
}
