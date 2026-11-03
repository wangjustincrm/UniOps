import {
  Badge, DateTimeCell, DetailList, LocalTable, MoneyCell, StatusCell, formatMoney, type ColumnDef,
} from '@/platform-ui'
import type { OrderDetail, PaymentRead, RefundRead } from './ordersApi'

const paymentColumns: ColumnDef<PaymentRead, unknown>[] = [
  { accessorKey: 'processed_at', header: 'When', cell: ({ row }) => <DateTimeCell value={row.original.processed_at} /> },
  { accessorKey: 'kind', header: 'Kind', cell: ({ row }) => <span className="capitalize">{row.original.kind}</span> },
  { accessorKey: 'gateway', header: 'Gateway', cell: ({ row }) => row.original.gateway ?? '—' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => (
    <span className="inline-flex items-center gap-1"><StatusCell value={row.original.status}
      tone={row.original.status === 'success' ? 'success' : row.original.status === 'pending' ? 'warning' : 'danger'} />
      {row.original.error_code && <span className="text-xs text-danger-600">{row.original.error_code.replace(/_/g, ' ')}</span>}</span>) },
  { accessorKey: 'amount', header: 'Amount', cell: ({ row }) => <MoneyCell amount={row.original.kind === 'refund' ? `-${row.original.amount}` : row.original.amount}
    currency={row.original.currency} /> },
  { accessorKey: 'authorization', header: 'Authorization', cell: ({ row }) => row.original.authorization ?? '—' },
  { accessorKey: 'payment_id', header: 'Payment ID', cell: ({ row }) => (
    <span className="inline-flex items-center gap-1">{row.original.payment_id ?? '—'}{row.original.test && <Badge variant="neutral">Test</Badge>}</span>) },
]

const refundColumns = (currency: string): ColumnDef<RefundRead, unknown>[] => [
  { accessorKey: 'refunded_at', header: 'When', cell: ({ row }) => <DateTimeCell value={row.original.refunded_at} /> },
  { accessorKey: 'note', header: 'Note', cell: ({ row }) => row.original.note ?? '' },
  { accessorKey: 'lines_amount', header: 'Lines (incl. tax)', cell: ({ row }) => <MoneyCell amount={row.original.lines_amount} currency={currency} /> },
  { accessorKey: 'shipping_amount', header: 'Shipping', cell: ({ row }) => <MoneyCell amount={row.original.shipping_amount} currency={currency} /> },
  { accessorKey: 'adjustments_amount', header: 'Adjustments', cell: ({ row }) => <MoneyCell amount={row.original.adjustments_amount} currency={currency} /> },
  { accessorKey: 'amount', header: 'Refunded', cell: ({ row }) => <MoneyCell amount={row.original.amount} currency={currency} /> },
]

function RefundDetail({ r, currency }: { r: RefundRead; currency: string }) {
  return (
    <div className="flex flex-col gap-1 text-xs">
      {r.lines.length > 0 && (
        <ul aria-label="Refunded lines">
          {r.lines.map((ln, i) => (
            <li key={i}>{ln.quantity} × {ln.sku ?? ln.title ?? `line ${ln.line_item_id}`} — {formatMoney(ln.subtotal, currency)}
              {Number(ln.total_tax) ? ` + ${formatMoney(ln.total_tax, currency)} tax` : ''}{ln.restock_type ? ` · ${ln.restock_type.replace('_', ' ')}` : ''}</li>
          ))}
        </ul>
      )}
      {r.adjustments.length > 0 && (
        <ul aria-label="Refund adjustments">
          {r.adjustments.map((a, i) => <li key={i}>{(a.kind ?? 'adjustment').replace('_', ' ')}{a.reason ? ` (${a.reason})` : ''}: {formatMoney(a.amount, currency)}
            {Number(a.tax_amount) ? ` + ${formatMoney(a.tax_amount, currency)} tax` : ''}</li>)}
        </ul>
      )}
    </div>
  )
}

/** A Shopify order's money (read-only from the store): the summary — status,
 *  sales channel, tags, gateways, discount codes, subtotal, discounts,
 *  shipping, taxes, totals, refunded and net — its payments and its refunds.
 *  No card data is ever stored. */
export function PaymentsAndRefunds({ o }: { o: OrderDetail }) {
  if (o.source_type !== 'SHOPIFY') return null
  const money = (v: string | null | undefined) => (v == null ? null : formatMoney(v, o.currency))
  const tags = (o.tags ?? '').split(',').map((t) => t.trim()).filter(Boolean)
  const refunds = o.refunds ?? []
  return (
    <section aria-label="Payments and refunds" className="flex flex-col gap-2">
      <h3 className="font-semibold text-neutral-800">Payments &amp; refunds</h3>
      <DetailList items={[
        ['Payment status', o.shopify_financial_status?.replace(/_/g, ' ') ?? null], ['Sales channel', o.source_name],
        ['Tags', tags.length ? <span key="t" className="flex flex-wrap gap-1">{tags.map((t) => <Badge key={t} variant="neutral">{t}</Badge>)}</span> : null],
        ['Gateways', (o.payment_gateways ?? []).join(', ') || null],
        ['Discount codes', (o.discount_codes ?? []).map((d) => `${d.code} (${formatMoney(d.amount, o.currency)})`).join(', ') || null],
        ['Subtotal', money(o.subtotal)], ['Discounts', money(o.discount)], ['Shipping', money(o.shipping)],
        ['Tax', o.tax_lines?.length
          ? <span key="tx">{money(o.tax)} ({o.tax_lines.map((t) => `${t.title} ${t.rate != null ? `${+(t.rate * 100).toFixed(3)}%` : ''}: ${formatMoney(t.amount, o.currency)}`).join(' · ')})</span>
          : money(o.tax)],
        ['Original total', money(o.original_total)], ['Refunded', money(o.total_refunded)],
        ['Net', o.net_amount == null ? null : <strong key="n">{money(o.net_amount)}</strong>],
      ]} />
      {o.net_amount == null && <p className="text-xs text-neutral-500">Not read from Shopify yet: the next pass of its connection fills it.</p>}
      <LocalTable<PaymentRead> ariaLabel="Payments" columns={paymentColumns} rows={o.payments ?? []} rowKey={(p) => p.id}
        emptyTitle="No payments recorded" />
      {refunds.length > 0 && (
        <LocalTable<RefundRead> ariaLabel="Refunds" columns={refundColumns(o.currency)} rows={refunds} rowKey={(r) => r.id}
          expand={(r) => (r.lines.length || r.adjustments.length ? <RefundDetail r={r} currency={o.currency} /> : null)}
          footer={['Total', null, null, null, null, formatMoney(refunds.reduce((t, r) => t + Number(r.amount), 0).toFixed(2), o.currency)]} />
      )}
    </section>
  )
}
