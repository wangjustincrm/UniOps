import { Badge, DateCell, LocalTable, MoneyCell, StatusCell, formatMoney, type ColumnDef, Hint } from '@/platform-ui'
import { VsPo } from '../InvoicePrices'
import { LineNo } from '../LineNo'
import { lineNumbers, type LineRead, type OrderDetail, type PackingRead } from '../ordersApi'

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
const lineColumns = (currency: string, numbers: Map<number, number>): ColumnDef<LineRead, unknown>[] => [
  { accessorKey: 'line_no', header: '#', cell: ({ row }) => <LineNo lineNo={row.original.line_no} numbers={numbers} /> },
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
  { accessorKey: 'shipped_ea', header: 'Shipped EA', cell: ({ row: { original: ln } }) => ea(ln.shipped_ea ?? ln.shipped_shadow_ea) },
  { accessorKey: 'backordered_ea', header: 'Backordered EA', cell: ({ row: { original: ln } }) => {
    const n = ln.backordered_ea ?? ln.backordered_shadow_ea
    return n ? <span className="font-medium text-warning-700">{n}</span> : ea(n)
  } },
  { accessorKey: 'delivered_qty', header: 'Delivered EA', cell: ({ row }) => ea(row.original.delivered_qty) },
  { id: 'invoiced', header: 'Invoiced', cell: ({ row }) => row.original.invoiced_qty == null ? '—'
    : `${row.original.invoiced_qty} ${row.original.invoice_unit ?? ''}` },
  // Invoiced: the invoice's price per its unit (CA). Before: what the draft will take, per order unit.
  { accessorKey: 'invoice_price', header: 'Invoice price', cell: ({ row: { original: ln } }) => ln.invoice_price == null ? '—' : (
    <span className="whitespace-nowrap"><MoneyCell amount={ln.invoice_price} currency={currency} />
      <Hint> /{ln.invoiced_qty != null ? ln.invoice_unit : (ln.order_unit ?? '?')}</Hint>
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
      {!p.shadow && !p.applied && <Badge variant="warning">Second 945</Badge>}
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
    <LocalTable<LineRead> ariaLabel="Order lines" columns={lineColumns(o.currency, lineNumbers(o))} rows={o.lines} rowKey={(ln) => ln.id}
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

/** The order's lines, ordered to invoiced. */
export function LinesTab({ o }: { o: OrderDetail }) {
  return (
    <>
      <Hint as="p">
        Ordered → shipped (945) → delivered → invoiced. ▸ shows the pallets and lots packed for a line.
        {o.has_invoice ? ' The invoice is drafted: change its prices on the invoice.'
          : ' The invoice price starts as the PO price; the invoice draft takes it.'}
      </Hint>
      <LifecycleTable o={o} />
    </>
  )
}
