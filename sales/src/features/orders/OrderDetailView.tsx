import { useQuery } from '@tanstack/react-query'
import { Badge, DetailList, ErrorView, Skeleton, StatusCell, formatDate, formatDateTime, formatMoney, queryKeys } from '@/platform-ui'
import { fetchOrder, ordersClient, type OrderRead } from './ordersApi'

// The badge shows the fulfillment status in the colour of the derived order state (D17).
const stateTone = { Draft: 'neutral', Active: 'info', 'On Hold': 'warning', Completed: 'success', Cancelled: 'neutral' } as const

export function StateBadge({ o }: { o: Pick<OrderRead, 'order_state' | 'fulfillment_status'> }) {
  return <StatusCell value={o.fulfillment_status} tone={stateTone[o.order_state]} />
}

/** One order: why it is held, its lines in EA, its source documents and what happened (FR-OB-53). */
export function OrderDetailView({ id }: { id: number }) {
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, id), queryFn: () => fetchOrder(id) })
  if (q.isLoading) return <Skeleton className="h-40 w-full" />
  if (q.error || !q.data) return <ErrorView error={q.error} />
  const o = q.data
  return (
    <div className="flex flex-col gap-5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StateBadge o={o} />
        <span className="text-neutral-500">{o.order_state}</span>
        {o.historical && <Badge variant="neutral">Historical</Badge>}
        {o.archive_suppressed && <Badge variant="warning">Archived in Shopify</Badge>}
      </div>
      {o.review_reason && (
        <section aria-label="Review reason" className="rounded-md border border-warning-200 bg-warning-50 p-2">
          <p className="font-medium text-warning-700">Needs review</p>
          <pre className="whitespace-pre-wrap text-xs text-warning-700">{o.review_reason}</pre>
        </section>
      )}
      <DetailList items={[
        ['Order', `${o.order_number} · ${o.partner_code} ${o.source_order_number ?? ''}`], ['Reference', o.source_reference],
        ['Source', o.source_type], ['Received', formatDateTime(o.first_received_at)], ['Paid (delay start)', formatDateTime(o.paid_at)],
        ['Payment', o.shopify_financial_status], ['Requested delivery', o.requested_delivery_date ? formatDate(o.requested_delivery_date) : null],
        ['Ship to', o.ship_to ? [o.ship_to.name, o.ship_to.address1, o.ship_to.city, o.ship_to.province, o.ship_to.zip].filter(Boolean).join(', ') : null],
        ['Total', formatMoney(o.total, o.currency)], ['Invoice', o.invoice_status],
      ]} />
      <section aria-label="Lines">
        <h3 className="mb-1 font-semibold text-neutral-800">Lines</h3>
        <table className="w-full text-xs">
          <thead><tr className="text-left text-neutral-500"><th>#</th><th>Item</th><th>Ordered</th><th>EA</th><th>Note</th></tr></thead>
          <tbody>
            {o.lines.map((ln) => (
              <tr key={ln.id} className={ln.removed ? 'border-t border-neutral-100 text-neutral-400' : 'border-t border-neutral-100'}>
                <td className="py-1">{ln.line_no}</td>
                <td>{ln.item_code ?? <span className="text-danger-600">unmatched</span>}</td>
                <td>{Number(ln.ordered_qty)} {ln.order_unit ?? '?'}</td>
                <td>{ln.qty_ea ?? '—'}</td>
                <td className={ln.problem && !ln.removed ? 'text-danger-600' : ''}>{ln.problem ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section aria-label="Documents">
        <h3 className="mb-1 font-semibold text-neutral-800">Source documents</h3>
        {o.documents.length === 0 ? <p className="text-neutral-500">None (entered by hand).</p> : (
          <ul className="text-xs">{o.documents.map((d) => <li key={d.id}>#{d.id} {d.kind} · {d.remote_name} · {formatDateTime(d.created_at)}</li>)}</ul>
        )}
      </section>
      <section aria-label="Timeline">
        <h3 className="mb-1 font-semibold text-neutral-800">Timeline</h3>
        <ol className="flex flex-col gap-1 text-xs">
          {o.timeline.map((e, i) => (
            <li key={i}>{formatDateTime(e.at)} · {e.user_name} · {e.reason ?? e.action}
              {e.after && 'fulfillment_status' in e.after && <> · {String(e.before?.fulfillment_status ?? '—')} → {String(e.after.fulfillment_status)}</>}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
