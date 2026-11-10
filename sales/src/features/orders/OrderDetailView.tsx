import { useContext, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, Badge, ConfirmAction, Hint, RecordSheet, RecordSheetHostContext, formatDate, formatMoney,
  queryKeys, type RecordFact, type RecordTab,
  compareRecord, useRefresh,
} from '@/platform-ui'
import { SOURCE_LABEL, downloadPacket, downloadPoPdf, fetchOrder, ordersClient, type OrderRead } from './ordersApi'
import { OrderHoldActions } from './OrderHoldActions'
import { OrderStatusBadge } from './OrderStatusCells'
import { statusDetailText } from './orderStatus'
import { PaymentsAndRefunds } from './PaymentsAndRefunds'
import { InvoicePricesForm } from './InvoicePrices'
import { Recheck } from './Recheck'
import { DraftActions } from './DraftActions'
import { Send940 } from './Send940'
import { ActivityTab } from './tabs/ActivityTab'
import { DocumentsTab } from './tabs/DocumentsTab'
import { InvoiceTab } from './tabs/InvoiceTab'
import { LinesTab } from './tabs/LinesTab'
import { OverviewTab } from './tabs/OverviewTab'
import { ShipmentsTab } from './tabs/ShipmentsTab'
import { releaseInvoice } from '@/features/invoices/invoicesApi'

const MANAGE_INVOICE = 'sales.invoice.manage'

export interface OrderSheetProps {
  o: OrderRead
  close: () => void
  /** More buttons for the action bar (e.g. Record a return). */
  extraActions?: ReactNode
  /** Tabs before the order's own (e.g. Confirm delivery). */
  extraTabs?: RecordTab[]
}

/** One order in the standard record sheet (FR-OB-53): the key numbers, the
 *  actions for its state, and its sections as tabs — Overview, Lines,
 *  Shipments & lots, Documents, Invoice, Payments (Shopify) and Activity. */
export function OrderSheet({ o, close, extraActions, extraTabs = [] }: OrderSheetProps) {
  const qc = useQueryClient()
  const host = useContext(RecordSheetHostContext)
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id) })
  const sheetRefresh = useRefresh(q, { queryKey: queryKeys.detail(ordersClient, o.id), compare: compareRecord, noChange: 'Up to date', auto: 'follow' })
  const [pricing, setPricing] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const d = q.data
  const live = d ?? o  // the list's row until the detail arrives
  const open = d ? d.lines.filter((ln) => !ln.removed) : []
  const unmatched = open.some((ln) => !ln.item_code || (ln.problem && !ln.removed))
  const backordered = live.backordered_ea ?? null
  const orderedEa = open.reduce((n, ln) => n + (ln.qty_ea ?? 0), 0)
  const shippedOf = (ln: (typeof open)[number]) => ln.shipped_ea ?? ln.shipped_shadow_ea
  const shippedEa = open.some((ln) => shippedOf(ln) != null) ? open.reduce((n, ln) => n + (shippedOf(ln) ?? 0), 0) : null
  const shipmentTrouble = !!d?.shipments.some((s) => (s.checks?.length ?? 0) > 0 || s.rows.some((r) => r.needs_review)
    || s.shopify_status === 'Failed')
  // A 940 that failed or was refused (a source file's note — e.g. a shadow-run 945 kept, not applied — is not a problem).
  const docTrouble = !!d?.documents.some((x) => x.direction === 'OUT'
    && (['Rejected', 'Failed'].includes(x.file_status ?? '') || x.ack_status === 'Rejected' || !!x.error))
  // Only an order that is invoiced here (not a Shopify order: Not Required) has an invoice price.
  const priceable = !!d && !d.has_invoice && d.invoice_status !== 'Not Required' && d.fulfillment_status !== 'Cancelled' && open.length > 0

  const tabs: RecordTab[] = !d ? [] : [
    ...extraTabs,
    { id: 'overview', label: 'Overview', tone: d.review_reason ? 'warning' : undefined, render: () => <OverviewTab o={d} /> },
    { id: 'lines', label: 'Lines', count: open.length, tone: unmatched || (backordered ?? 0) > 0 ? 'warning' : undefined, render: () => (
      pricing ? (
        <InvoicePricesForm o={d} onCancel={() => setPricing(false)} onSaved={async (next) => {
          qc.setQueryData(queryKeys.detail(ordersClient, o.id), next)
          setPricing(false)
          await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
        }} />
      ) : (
        <LinesTab o={d} />
      )
    ) },
    { id: 'shipments', label: 'Shipments & lots', count: d.shipments.length, tone: shipmentTrouble ? 'warning' : undefined,
      empty: d.shipments.length === 0 && 'No 945 yet: the warehouse has not reported a shipment.',
      render: () => <ShipmentsTab o={d} /> },
    { id: 'documents', label: 'Documents', count: d.documents.length, tone: docTrouble ? 'danger' : undefined,
      render: () => <DocumentsTab o={d} /> },
    { id: 'invoice', label: 'Invoice', hidden: d.invoice_status === 'Not Required' && !d.has_invoice,
      tone: ['Blocked', 'Failed'].includes(d.invoice_status) ? 'warning' : undefined, render: () => <InvoiceTab o={d} /> },
    { id: 'payments', label: 'Payments', hidden: d.source_type !== 'SHOPIFY', count: (d.payments?.length ?? 0) || undefined,
      tone: Number(d.total_refunded ?? 0) > 0 ? 'warning' : undefined, render: () => <PaymentsAndRefunds o={d} /> },
    { id: 'activity', label: 'Activity', count: d.timeline.length, empty: d.timeline.length === 0 && 'Nothing recorded yet',
      render: () => <ActivityTab o={d} /> },
  ]

  const facts: RecordFact[] = !d ? [] : [
    { label: 'Total', value: formatMoney(d.total_amount ?? d.total, d.currency), hint: `${open.length} ${open.length === 1 ? 'line' : 'lines'}` },
    ...(d.source_type === 'SHOPIFY' && d.net_amount != null ? [{
      label: 'Net', value: formatMoney(d.net_amount, d.currency), tone: Number(d.total_refunded ?? 0) > 0 ? 'warning' as const : 'neutral' as const,
      hint: Number(d.total_refunded ?? 0) > 0 ? `${formatMoney(d.total_refunded!, d.currency)} refunded` : 'nothing refunded' }] : []),
    { label: 'Ordered EA', value: orderedEa },
    { label: 'Shipped EA', value: shippedEa == null ? '—' : shippedEa, hint: shippedEa == null ? 'no 945 yet' : undefined },
    ...(backordered != null ? [{ label: 'Backordered EA', value: backordered, tone: backordered > 0 ? 'warning' as const : 'neutral' as const }] : []),
    { label: 'Requested delivery', value: d.requested_delivery_date ? formatDate(d.requested_delivery_date) : '—' },
  ]

  const isDraft = live.source_type === 'MANUAL' && live.fulfillment_status === 'Draft'
  const actions = d ? (
    <>
      {d.fulfillment_status === 'Review' && <Recheck o={d} bare onResolved={close} />}
      {isDraft && <DraftActions o={d} close={close} />}
      <Send940 o={d} />
      <OrderHoldActions o={d} />
      {priceable && !pricing && (
        <ActionButton size="sm" variant="secondary" permission={MANAGE_INVOICE}
          onAction={() => { setPricing(true); host?.onTabChange?.('lines') }}>Edit invoice prices</ActionButton>
      )}
      {d.invoice_status === 'Blocked' && (
        <ActionButton size="sm" permission={MANAGE_INVOICE} onAction={() => setReleasing(true)}>Release invoice…</ActionButton>
      )}
      {extraActions}
    </>
  ) : undefined

  return (
    <>
      <RecordSheet
        title={live.source_reference ?? `#${live.order_number}`}
        subtitle={[...new Set([`Internal no. ${live.order_number}`, live.partner_name ?? live.partner_code,
          SOURCE_LABEL[live.source_type] ?? live.source_type])].concat(live.source_order_number ? [`PO ${live.source_order_number}`] : []).join(' · ')}
        badges={<>
          <span className="flex flex-col gap-0.5">
            <OrderStatusBadge o={live} />
            {statusDetailText(live) && <Hint>Detail: {statusDetailText(live)}</Hint>}
          </span>
          {live.historical && <Badge variant="neutral">Historical</Badge>}
        </>}
        size="xl" loading={q.isLoading} error={q.error}
        refresh={sheetRefresh}
        facts={facts} actions={actions} tabs={tabs}
        more={[
          { label: 'Purchase order (PDF)', onSelect: () => downloadPoPdf(live), failPrefix: 'Download failed' },
          { label: 'Audit packet: every file and PDF (zip)', onSelect: () => downloadPacket(live), failPrefix: 'Download failed' },
        ]}
      />
      {d && (
        <ConfirmAction open={releasing} onOpenChange={setReleasing} title={`Release the invoice of ${live.source_reference ?? live.order_number}?`}
          description="It goes back to Ready, to be sent." confirmLabel="Release" reason={{ label: 'Why it can go ahead' }}
          onConfirm={async (why) => {
            await releaseInvoice(d.id, why)
            await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) })
            await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
          }} />
      )}
    </>
  )
}

