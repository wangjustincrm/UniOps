import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, Badge, DateCell, ErrorMessage, StatusCell, queryKeys } from '@/platform-ui'
import { ordersClient, retryShopify, type ShipmentRead } from './ordersApi'

const errorMessages: Record<string, string> = {
  'shipment.shadow': 'The shadow run is on (or this 945 came during it): nothing is written to Shopify.',
  'shipment.not_failed': 'The Shopify write is not failed.',
  'auth.forbidden': 'You do not have permission to retry Shopify writes.',
}

function Retry({ s, orderId }: { s: ShipmentRead; orderId: number }) {
  const qc = useQueryClient()
  const m = useMutation({
    mutationFn: () => retryShopify(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, orderId) }),
  })
  return (
    <span className="inline-flex flex-col gap-1">
      <ActionButton size="sm" variant="secondary" permission="sales.order.operate" onAction={() => m.mutateAsync().catch(() => {})}>
        Retry in Shopify
      </ActionButton>
      <ErrorMessage error={m.error} messages={errorMessages} />
    </span>
  )
}

/** The order's 945s: every pallet row with its lot (FR-OB-25b), the checks
 *  that notified (FR-OB-25d) and the Shopify write-back (FR-OB-11). */
export function Shipments({ shipments, orderId }: { shipments: ShipmentRead[]; orderId: number }) {
  if (shipments.length === 0) return null
  return (
    <section aria-label="Shipments" className="flex flex-col gap-3">
      <h3 className="font-semibold text-neutral-800">Shipments (945)</h3>
      {shipments.map((s) => (
        <div key={s.id} aria-label={`Shipment ${s.id}`} role="group" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span>Shipped <DateCell value={s.ship_date} /> ({s.ship_date_source ?? 'no date'})</span>
            {s.carrier && <span>· {s.carrier} {s.mode ?? ''}</span>}
            {s.tracking?.length ? <span>· tracking {s.tracking.join(', ')}</span> : <span>· no tracking</span>}
            {s.shadow && <Badge variant="neutral">Shadow run: not applied</Badge>}
            {!s.shadow && !s.applied && <Badge variant="warning">Not applied (second 945)</Badge>}
            <span>· Shopify <StatusCell value={s.shopify_status} /></span>
            {(s.shopify_status === 'Failed' || s.shopify_status === 'Suppressed') && !s.shadow && <Retry s={s} orderId={orderId} />}
          </div>
          {s.shopify_error && <p className="text-danger-600">{s.shopify_error}</p>}
          <table className="w-full">
            <thead><tr className="text-left text-neutral-500"><th>Pallet (SSCC)</th><th>Item</th><th>Lot</th><th>Expiry</th><th>Shipped / ordered EA</th><th /></tr></thead>
            <tbody>
              {s.rows.map((r) => (
                <tr key={`${r.lx_index}-${r.w12_index}`} className="border-t border-neutral-100">
                  <td className="py-0.5">{r.sscc ?? `LX ${r.lx_index}`}</td>
                  <td>{r.item_code ?? '—'}</td>
                  <td>{r.lot ?? '—'}</td>
                  <td><DateCell value={r.expiry} /></td>
                  <td>{r.qty_shipped_ea} / {r.qty_ordered_ea ?? '—'}</td>
                  <td>{r.needs_review && <Badge variant="danger">matches no order line</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {s.checks && s.checks.length > 0 && (
            <ul aria-label="945 checks" className="text-warning-700">
              {s.checks.map((c, i) => <li key={i}>{c.check}: {c.message}</li>)}
            </ul>
          )}
        </div>
      ))}
    </section>
  )
}
