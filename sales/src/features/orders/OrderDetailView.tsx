import { useContext, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Download } from 'lucide-react'
import {
  ActionButton, AddressView, Badge, Button, ConfirmAction, CopyChip, DateCell, DateTimeCell, DetailList, ErrorMessage, IconButton,
  LocalTable, MoneyCell, RecordSheet, RecordSheetHostContext, Section, StatusCell, Timeline, formatDate, formatDateTime, formatMoney,
  queryKeys, requestOpen,
  type ColumnDef, type RecordFact, type RecordTab, type TimelineItem,
} from '@/platform-ui'
import { fetchOrder, ordersClient, type LineRead, type OrderDetail, type OrderDocument, type OrderRead, type PackingRead } from './ordersApi'
import { Shipments } from './Shipments'
import { PaymentsAndRefunds } from './PaymentsAndRefunds'
import { InvoicePricesForm, VsPo } from './InvoicePrices'
import { Recheck } from './Recheck'
import { DraftActions } from './DraftActions'
import { Send940 } from './Send940'
import { partnerLabel } from '@/features/common/labels'
import { invoicesClient, releaseInvoice } from '@/features/invoices/invoicesApi'
import { downloadDocument } from '@/features/operations/operationsApi'

// The badge shows the fulfillment status in the colour of the derived order state (D17).
const stateTone = { Draft: 'neutral', Active: 'info', 'On Hold': 'warning', Completed: 'success', Cancelled: 'neutral' } as const

// A short 945 closes the fulfilment (Shipped, FR-OB-25); what it left unshipped is marked beside it.
export function StateBadge({ o }: { o: Pick<OrderRead, 'order_state' | 'fulfillment_status' | 'backordered_ea'> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusCell value={o.fulfillment_status} tone={stateTone[o.order_state]} />
      {o.fulfillment_status === 'Shipped' && (o.backordered_ea ?? 0) > 0 && <Badge variant="warning">Partially shipped</Badge>}
    </span>
  )
}

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

/** A line's amount at the PO price: ordered quantity × unit price (in the order's unit). */
function lineAmount(ln: LineRead): number | null {
  return ln.unit_price == null || ln.removed ? null : Number(ln.ordered_qty) * Number(ln.unit_price)
}

const stateTones: Record<string, 'neutral' | 'info' | 'warning' | 'success' | 'danger'> = {
  Removed: 'neutral', Open: 'neutral', Shipped: 'info', 'Short shipped': 'warning', 'Over shipped': 'warning',
  Delivered: 'info', 'Short delivered': 'warning', Invoiced: 'success',
}
const ea = (n: number | null | undefined) => (n == null ? '—' : n)
const sum = (lines: LineRead[], f: (ln: LineRead) => number | null | undefined) =>
  lines.filter((ln) => !ln.removed).reduce((t, ln) => t + Number(f(ln) ?? 0), 0)

// One row per line, ordered → shipped (945) → delivered → invoiced; its ▸ opens
// the 945 rows packed for it. The less-used columns start hidden (the eye shows them).
const lineColumns = (currency: string): ColumnDef<LineRead, unknown>[] => [
  { accessorKey: 'line_no', header: '#' },
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? <span className="text-danger-600">unmatched</span> },
  { id: 'ordered', header: 'Ordered', cell: ({ row }) => `${Number(row.original.ordered_qty)} ${row.original.order_unit ?? '?'}` },
  { accessorKey: 'order_unit', header: 'Unit', cell: ({ row }) => row.original.order_unit ?? '?' },
  { accessorKey: 'unit_price', header: 'Unit price (PO)', cell: ({ row }) => row.original.unit_price == null ? '—'
    : <MoneyCell amount={row.original.unit_price} currency={currency} /> },
  { id: 'amount', header: 'Amount', cell: ({ row }) => {
    const a = lineAmount(row.original)
    return a == null ? '—' : <MoneyCell amount={a.toFixed(2)} currency={currency} />
  } },
  { accessorKey: 'discount_amount', header: 'Discount', cell: ({ row }) => row.original.discount_amount == null ? '—'
    : <MoneyCell amount={row.original.discount_amount} currency={currency} /> },
  { accessorKey: 'tax_amount', header: 'Tax', cell: ({ row }) => row.original.tax_amount == null ? '—'
    : <MoneyCell amount={row.original.tax_amount} currency={currency} /> },
  { accessorKey: 'qty_ea', header: 'EA', cell: ({ row }) => ea(row.original.qty_ea) },
  { accessorKey: 'shipped_ea', header: 'Shipped EA', cell: ({ row }) => ea(row.original.shipped_ea) },
  { accessorKey: 'backordered_ea', header: 'Backordered EA', cell: ({ row }) => (row.original.backordered_ea
    ? <span className="font-medium text-warning-700">{row.original.backordered_ea}</span> : ea(row.original.backordered_ea)) },
  { accessorKey: 'delivered_qty', header: 'Delivered EA', cell: ({ row }) => ea(row.original.delivered_qty) },
  { id: 'invoiced', header: 'Invoiced', cell: ({ row }) => row.original.invoiced_qty == null ? '—'
    : `${row.original.invoiced_qty} ${row.original.invoice_unit ?? ''}` },
  // Invoiced: the invoice's price per its unit (CA). Before: what the draft will take, per order unit.
  { accessorKey: 'invoice_price', header: 'Invoice price', cell: ({ row: { original: ln } }) => ln.invoice_price == null ? '—' : (
    <span className="whitespace-nowrap"><MoneyCell amount={ln.invoice_price} currency={currency} />
      <span className="text-xs text-neutral-500"> /{ln.invoiced_qty != null ? ln.invoice_unit : (ln.order_unit ?? '?')}</span>
      {ln.invoiced_qty == null && <VsPo price={ln.invoice_price} po={ln.unit_price} currency={currency} />}
    </span>) },
  { accessorKey: 'invoice_amount', header: 'Invoice amount', cell: ({ row }) => row.original.invoice_amount == null ? '—'
    : <MoneyCell amount={row.original.invoice_amount} currency={currency} /> },
  { accessorKey: 'state', header: 'Line status', cell: ({ row }) => row.original.state
    ? <StatusCell value={row.original.state} tone={stateTones[row.original.state]} /> : '' },
  { accessorKey: 'problem', header: 'Note', cell: ({ row }) => (
    <span className={row.original.problem && !row.original.removed ? 'text-danger-600' : ''}>{row.original.problem ?? ''}</span>
  ) },
]
const HIDDEN_LINE_COLUMNS = ['line_no', 'order_unit', 'discount_amount', 'tax_amount']

const packingColumns: ColumnDef<PackingRead, unknown>[] = [
  { accessorKey: 'sscc', header: 'Pallet (SSCC)', cell: ({ row }) => row.original.sscc ?? '—' },
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ?? '—' },
  { accessorKey: 'expiry', header: 'Expiry', cell: ({ row }) => <DateCell value={row.original.expiry} /> },
  { accessorKey: 'qty_shipped_ea', header: 'Qty EA' },
  { accessorKey: 'tracking', header: 'Tracking', cell: ({ row }) => row.original.tracking ?? '—' },
  { accessorKey: 'ship_date', header: 'Ship date', cell: ({ row }) => <DateCell value={row.original.ship_date} /> },
  { id: 'doc', header: '945', cell: ({ row: { original: p } }) => (
    <span className="inline-flex items-center gap-1">#{p.document_id}
      {p.shadow ? <Badge variant="neutral">Shadow run</Badge> : !p.applied && <Badge variant="warning">Second 945</Badge>}
    </span>) },
]

function Packing({ rows }: { rows: PackingRead[] }) {
  return (
    <LocalTable<PackingRead> ariaLabel="Packed for this line" columns={packingColumns} rows={rows}
      rowKey={(p, i) => `${p.shipment_id}-${i}`}
      footer={[null, null, 'Total', rows.reduce((n, p) => n + p.qty_shipped_ea, 0), null, null, null]} />
  )
}

function LifecycleTable({ o }: { o: OrderDetail }) {
  const money = (n: number) => formatMoney(n.toFixed(2), o.currency)
  const hasNotes = o.lines.some((ln) => ln.problem && !ln.removed)
  return (
    <LocalTable<LineRead> ariaLabel="Order lines" columns={lineColumns(o.currency)} rows={o.lines} rowKey={(ln) => ln.id}
      rowAttrs={(ln) => (ln.removed ? { className: 'text-neutral-400', 'data-removed': true } : {})}
      initiallyHidden={hasNotes ? HIDDEN_LINE_COLUMNS : [...HIDDEN_LINE_COLUMNS, 'problem']}
      expand={(ln) => (ln.packing?.length ? <Packing rows={ln.packing} /> : null)}
      footer={[null, 'Total', null, null, null, money(sum(o.lines, lineAmount)), null, null, sum(o.lines, (ln) => ln.qty_ea),
        sum(o.lines, (ln) => ln.shipped_ea),
        o.lines.some((ln) => ln.backordered_ea != null) ? sum(o.lines, (ln) => ln.backordered_ea) : null,
        sum(o.lines, (ln) => ln.delivered_qty), null, null,
        o.lines.some((ln) => ln.invoice_amount != null) ? money(sum(o.lines, (ln) => Number(ln.invoice_amount ?? 0))) : null,
        null, null]} />
  )
}


// The order's files: the sources (Shopify page, 850), the 940s with their 997
// apart (Sent is not Accepted, FR-OB-22), each downloadable.
function DocumentsTable({ docs }: { docs: OrderDocument[] }) {
  const [error, setError] = useState<unknown>(null)
  const columns: ColumnDef<OrderDocument, unknown>[] = [
    { accessorKey: 'created_at', header: 'Created', cell: ({ row }) => <DateTimeCell value={row.original.created_at} /> },
    { accessorKey: 'kind', header: 'Kind', cell: ({ row: { original: d } }) => <span className="whitespace-nowrap">{d.kind}{d.direction === 'OUT' ? ' (sent)' : ''}</span> },
    { accessorKey: 'remote_name', header: 'File', cell: ({ row }) => <span className="break-all">{row.original.remote_name ?? '—'}</span> },
    { accessorKey: 'st02', header: 'Control', cell: ({ row }) => row.original.st02 ?? '' },
    { accessorKey: 'file_status', header: 'File status', cell: ({ row }) => (row.original.file_status ? <StatusCell value={row.original.file_status} /> : '') },
    { accessorKey: 'ack_status', header: '997', cell: ({ row: { original: d } }) => (
      d.direction !== 'OUT' ? '' : d.file_status === 'Sent' || d.ack_status !== 'Waiting' ? <StatusCell value={d.ack_status} /> : '—') },
    { accessorKey: 'sent_at', header: 'Sent', cell: ({ row }) => <DateTimeCell value={row.original.sent_at} /> },
    { id: 'note', header: 'Note', cell: ({ row }) => <span className="break-words text-xs">{row.original.error ?? row.original.ack_detail ?? ''}</span> },
    { id: 'download', header: '', cell: ({ row: { original: d } }) => (
      <IconButton label={`Download ${d.remote_name ?? d.kind}`} icon={<Download className="h-4 w-4" />}
        onClick={() => { downloadDocument(d.id, d.remote_name ?? `document-${d.id}`).catch(setError) }} />) },
  ]
  return (
    <>
      <LocalTable<OrderDocument> ariaLabel="Documents" columns={columns} rows={docs} rowKey={(d) => d.id} initiallyHidden={['st02']} />
      <ErrorMessage error={error} />
    </>
  )
}

// Every pallet row the warehouse packed for the order, with its lot and
// tracking (a recall's view of this order).
const lotColumns: ColumnDef<PackingRead & { item: string | null }, unknown>[] = [
  { accessorKey: 'item', header: 'Item' },
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ? <CopyChip text={row.original.lot} /> : '—' },
  { accessorKey: 'expiry', header: 'Expiry', cell: ({ row }) => <DateCell value={row.original.expiry} /> },
  { accessorKey: 'qty_shipped_ea', header: 'Qty EA', cell: ({ row }) => <span className="tabular-nums">{row.original.qty_shipped_ea}</span> },
  { accessorKey: 'sscc', header: 'Pallet (SSCC)', cell: ({ row }) => row.original.sscc ?? '—' },
  { accessorKey: 'tracking', header: 'Tracking', cell: ({ row }) => row.original.tracking
    ? <span className="flex flex-wrap gap-1">{row.original.tracking.split(', ').map((t) => <CopyChip key={t} text={t} />)}</span> : '—' },
  { accessorKey: 'ship_date', header: 'Ship date', cell: ({ row }) => <DateCell value={row.original.ship_date} /> },
  { id: 'doc', header: '945', cell: ({ row: { original: p } }) => (
    <span className="inline-flex items-center gap-1">#{p.document_id}
      {p.shadow ? <Badge variant="neutral">Shadow run</Badge> : !p.applied && <Badge variant="warning">Second 945</Badge>}
    </span>) },
]

function Lots({ o }: { o: OrderDetail }) {
  const rows = o.lines.flatMap((ln) => (ln.packing ?? []).map((p) => ({ ...p, item: ln.item_code })))
  if (rows.length === 0) return null
  return (
    <Section title="Pallets and lots">
      <LocalTable ariaLabel="Pallets and lots" columns={lotColumns} rows={rows} rowKey={(p, i) => `${p.shipment_id}-${i}`}
        sortable={['item', 'lot', 'expiry', 'ship_date']}
        footer={[null, null, 'Total', rows.reduce((n, p) => n + p.qty_shipped_ea, 0), null, null, null, null]} />
    </Section>
  )
}

/** The order's invoice: status, number, method, total, the file it was sent as; opened on Invoices. */
function OrderInvoice({ o }: { o: OrderDetail }) {
  const navigate = useNavigate()
  const [error, setError] = useState<unknown>(null)
  const q = useQuery({
    queryKey: [...queryKeys.list(invoicesClient), 'order', o.id],
    queryFn: () => invoicesClient.list({ page: 1, pageSize: 1, filters: [{ field: 'order_id', op: 'eq', value: String(o.id) }] }),
    enabled: !!o.has_invoice,
  })
  const inv = q.data?.items[0]
  return (
    <Section title="Invoice">
      <DetailList items={[
        ['Invoice status', <StatusCell key="s" value={o.invoice_status} />],
        ['Invoice', inv ? inv.invoice_number : o.has_invoice ? (q.isLoading ? 'Loading…' : '—') : 'Not built yet (built at delivery confirmation)'],
        ['Method', inv?.method ?? null], ['Invoice date', inv ? formatDate(inv.invoice_date) : null],
        ['Total', inv ? formatMoney(inv.total, inv.currency) : null], ['Delivery number', inv?.delivery_number ?? null],
        ['Sent', inv?.sent_at ? `${inv.send_count}× · last ${formatDateTime(inv.sent_at)}` : inv ? 'Not yet' : null],
        ['Problem', inv?.error ?? null],
      ]} />
      {inv && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => { requestOpen('/invoices', inv.id); navigate('/invoices') }}>Open invoice {inv.invoice_number}</Button>
          {inv.document_id != null && (
            <Button size="sm" variant="secondary" onClick={() => { downloadDocument(inv.document_id!, `${inv.invoice_number}-${inv.method}`).catch(setError) }}>
              <Download className="mr-1 h-3.5 w-3.5" />The file sent ({inv.method === '810_JSON' ? '810' : inv.method})
            </Button>
          )}
        </div>
      )}
      <ErrorMessage error={q.error ?? error} />
    </Section>
  )
}

const timelineTone = (e: OrderDetail['timeline'][number]): TimelineItem['tone'] => {
  const st = String(e.after?.fulfillment_status ?? e.after?.invoice_status ?? '')
  if (e.action === 'create') return 'success'
  if (['On Hold', 'Review', 'Blocked', 'Failed', '940 Rejected', '940 Failed', '940 Timeout'].includes(st)) return 'warning'
  if (st === 'Cancelled') return 'danger'
  return st ? 'info' : 'neutral'
}
const ENTITY: Record<string, string> = { sales_orders: 'Order', sales_order_lines: 'Line', sales_invoices: 'Invoice', sales_shipments: 'Shipment' }
const ACTION: Record<string, string> = { create: 'created', update: 'updated', delete: 'deleted' }
const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

function Activity({ o }: { o: OrderDetail }) {
  return (
    <Timeline items={o.timeline.map((e, i) => {
      const keys = Object.keys(e.after ?? {}).filter((k) => !['version', 'updated_at', 'updated_by', 'updated_by_name'].includes(k))
      return {
        key: i, when: formatDateTime(e.at), who: e.user_name, tone: timelineTone(e),
        title: e.reason ?? `${ENTITY[e.entity] ?? e.entity.replace('sales_', '').replace(/_/g, ' ')} ${ACTION[e.action] ?? e.action}`,
        body: e.action === 'update' && keys.length > 0 ? (
          <ul>{keys.slice(0, 6).map((k) => <li key={k} className="break-all"><span className="font-medium">{k.replace(/_/g, ' ')}</span>: {show(e.before?.[k])} → {show(e.after?.[k])}</li>)}</ul>
        ) : undefined,
      }
    })} />
  )
}

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
  const [pricing, setPricing] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const d = q.data
  const live = d ?? o  // the list's row until the detail arrives
  const open = d ? d.lines.filter((ln) => !ln.removed) : []
  const unmatched = open.some((ln) => !ln.item_code || (ln.problem && !ln.removed))
  const backordered = live.backordered_ea ?? null
  const orderedEa = open.reduce((n, ln) => n + (ln.qty_ea ?? 0), 0)
  const shippedEa = open.some((ln) => ln.shipped_ea != null) ? open.reduce((n, ln) => n + (ln.shipped_ea ?? 0), 0) : null
  const shipmentTrouble = !!d?.shipments.some((s) => (s.checks?.length ?? 0) > 0 || s.rows.some((r) => r.needs_review)
    || (s.shopify_status === 'Failed' && !s.shadow))
  // A 940 that failed or was refused (a source file's note — e.g. a shadow-run 945 kept, not applied — is not a problem).
  const docTrouble = !!d?.documents.some((x) => x.direction === 'OUT'
    && (['Rejected', 'Failed'].includes(x.file_status ?? '') || x.ack_status === 'Rejected' || !!x.error))
  // Only an order that is invoiced here (not a Shopify order: Not Required) has an invoice price.
  const priceable = !!d && !d.has_invoice && d.invoice_status !== 'Not Required' && d.fulfillment_status !== 'Cancelled' && open.length > 0
  const refresh = async () => {
    await q.refetch()
  }

  const tabs: RecordTab[] = !d ? [] : [
    ...extraTabs,
    { id: 'overview', label: 'Overview', tone: d.review_reason ? 'warning' : undefined, render: () => (
      <>
        {d.review_reason && (
          <section aria-label="Review reason" className="flex flex-col gap-1 rounded-lg border border-warning-200 bg-warning-50 p-3">
            <p className="font-medium text-warning-700">Needs review</p>
            <pre className="whitespace-pre-wrap text-xs text-warning-700">{d.review_reason}</pre>
            <p className="text-xs text-warning-700">Fix the cause in the Item Master (an item, a unit, a partner code, a DC route), then Re-check.</p>
          </section>
        )}
        <Section title="Order">
          <DetailList items={[
            ['Order ref', d.source_reference], ['Internal no.', String(d.order_number)],
            ['Partner', partnerLabel(d.partner_name, d.partner_code)], ['Customer PO / reference', d.source_order_number],
            ['Source', d.source_type], ['Received', formatDateTime(d.first_received_at)], ['Paid (delay start)', formatDateTime(d.paid_at)],
            ['Requested delivery', d.requested_delivery_date ? formatDate(d.requested_delivery_date) : null],
            ['Total', formatMoney(d.total_amount ?? d.total, d.currency)], ['Invoice', d.invoice_status],
            ['Notes', d.notes],
          ]} />
        </Section>
        <div className="grid gap-4 md:grid-cols-2">
          <Section title="Ship to">
            <AddressView value={d.ship_to} note={shipToOrigin(d)} />
            {(d.ship_to_flags ?? []).map((f) => <Badge key={f} variant="warning">{f}</Badge>)}
          </Section>
          <Section title="Bill to"><AddressView value={d.bill_to} note={billToOrigin(d)} /></Section>
        </div>
      </>
    ) },
    { id: 'lines', label: 'Lines', count: open.length, tone: unmatched || (backordered ?? 0) > 0 ? 'warning' : undefined, render: () => (
      pricing ? (
        <InvoicePricesForm o={d} onCancel={() => setPricing(false)} onSaved={async (next) => {
          qc.setQueryData(queryKeys.detail(ordersClient, o.id), next)
          setPricing(false)
          await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
        }} />
      ) : (
        <>
          <p className="text-xs text-neutral-500">
            Ordered → shipped (945) → delivered → invoiced. ▸ shows the pallets and lots packed for a line.
            {d.has_invoice ? ' The invoice is drafted: change its prices on the invoice.'
              : ' The invoice price starts as the PO price; the invoice draft takes it.'}
          </p>
          <LifecycleTable o={d} />
        </>
      )
    ) },
    { id: 'shipments', label: 'Shipments & lots', count: d.shipments.length, tone: shipmentTrouble ? 'warning' : undefined,
      empty: d.shipments.length === 0 && 'No 945 yet: the warehouse has not reported a shipment.',
      render: () => (
        <>
          <Shipments shipments={d.shipments} orderId={d.id} />
          <Lots o={d} />
        </>
      ) },
    { id: 'documents', label: 'Documents', count: d.documents.length, tone: docTrouble ? 'danger' : undefined,
      empty: d.documents.length === 0 && 'No document: entered by hand and not sent yet.',
      render: () => <DocumentsTable docs={d.documents} /> },
    { id: 'invoice', label: 'Invoice', hidden: d.invoice_status === 'Not Required' && !d.has_invoice,
      tone: ['Blocked', 'Failed'].includes(d.invoice_status) ? 'warning' : undefined, render: () => <OrderInvoice o={d} /> },
    { id: 'payments', label: 'Payments', hidden: d.source_type !== 'SHOPIFY', count: (d.payments?.length ?? 0) || undefined,
      tone: Number(d.total_refunded ?? 0) > 0 ? 'warning' : undefined, render: () => <PaymentsAndRefunds o={d} /> },
    { id: 'activity', label: 'Activity', count: d.timeline.length, empty: d.timeline.length === 0 && 'Nothing recorded yet',
      render: () => <Activity o={d} /> },
  ]

  const facts: RecordFact[] = !d ? [] : [
    { label: 'Total', value: formatMoney(d.total_amount ?? d.total, d.currency), hint: d.currency },
    ...(d.source_type === 'SHOPIFY' && d.net_amount != null ? [{
      label: 'Net', value: formatMoney(d.net_amount, d.currency), tone: Number(d.total_refunded ?? 0) > 0 ? 'warning' as const : 'neutral' as const,
      hint: Number(d.total_refunded ?? 0) > 0 ? `${formatMoney(d.total_refunded!, d.currency)} refunded` : 'nothing refunded' }] : []),
    { label: 'Ordered EA', value: orderedEa },
    { label: 'Shipped EA', value: shippedEa == null ? '—' : shippedEa, hint: shippedEa == null ? 'no 945 applied' : undefined },
    ...(backordered != null ? [{ label: 'Backordered EA', value: backordered, tone: backordered > 0 ? 'warning' as const : 'neutral' as const }] : []),
    { label: 'Invoice', value: d.invoice_status, tone: ['Blocked', 'Failed'].includes(d.invoice_status) ? 'warning' : d.invoice_status === 'Sent' ? 'success' : 'neutral' },
    { label: 'Requested delivery', value: d.requested_delivery_date ? formatDate(d.requested_delivery_date) : '—' },
  ]

  const isDraft = live.source_type === 'MANUAL' && live.fulfillment_status === 'Draft'
  const actions = d ? (
    <>
      {d.fulfillment_status === 'Review' && <Recheck o={d} bare onResolved={close} />}
      {isDraft && <DraftActions o={d} close={close} />}
      <Send940 o={d} />
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
          <StateBadge o={live} />
          <span className="text-xs text-neutral-500">{live.order_state}</span>
          {live.historical && <Badge variant="neutral">Historical</Badge>}
          {live.archive_suppressed && <Badge variant="warning">Archived in Shopify</Badge>}
        </>}
        size="xl" loading={q.isLoading} error={q.error}
        onRefresh={refresh} updatedAt={q.dataUpdatedAt}
        facts={facts} actions={actions} tabs={tabs}
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

const SOURCE_LABEL: Record<string, string> = { SHOPIFY: 'Shopify', EDI_850: 'Retailer 850', MANUAL: 'Manual' }
