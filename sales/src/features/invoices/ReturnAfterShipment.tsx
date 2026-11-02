import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ErrorMessage, FormField, Input, NumberInput, Select, queryKeys, useCan } from '@/platform-ui'
import { fetchOrder, ordersClient, type OrderRead } from '@/features/orders/ordersApi'
import { recordReturn } from './invoicesApi'

const KINDS = [{ value: 'Returned', label: 'Returned' }, { value: 'Cancelled after shipment', label: 'Cancelled after shipment' }]

/** FR-OB-19: a retailer order cancelled or returned after shipment — recorded
 *  with quantities and reason; the invoice holds and a task is raised. */
export function ReturnAfterShipment({ o }: { o: OrderRead }) {
  const can = useCan('sales.invoice.manage')
  const qc = useQueryClient()
  const shipped = o.fulfillment_status === 'Shipped' || o.fulfillment_status === 'On Hold'
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id), enabled: shipped && can })
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<string | null>('Returned')
  const [reason, setReason] = useState('')
  const [qty, setQty] = useState<Record<number, string>>({})
  const m = useMutation({
    mutationFn: () => recordReturn({
      order_id: o.id, kind: kind as 'Returned' | 'Cancelled after shipment', reason: reason.trim(),
      lines: Object.entries(qty).filter(([, v]) => Number(v) > 0).map(([id, v]) => ({ order_line_id: Number(id), qty_ea: Number(v) })),
    }),
    onSuccess: async () => { setOpen(false); await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) }) },
  })
  if (!shipped || !can || o.source_type !== 'EDI_850') return null
  if (!open) return <ActionButton size="sm" variant="secondary" permission="sales.invoice.manage" onAction={() => setOpen(true)}>Record a return or cancellation…</ActionButton>
  const lines = (q.data?.lines ?? []).filter((l) => l.shipped_ea)
  return (
    <section aria-label="Return after shipment" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2 text-sm">
      <FormField label="What happened" htmlFor="ret-kind"><Select id="ret-kind" value={kind} onChange={setKind} options={KINDS} /></FormField>
      {lines.map((l) => (
        <FormField key={l.id} label={`${l.item_code}: EA (shipped ${l.shipped_ea})`} htmlFor={`ret-${l.id}`}>
          <NumberInput id={`ret-${l.id}`} min={0} max={l.shipped_ea ?? undefined} value={qty[l.id] ?? ''} onChange={(e) => setQty((x) => ({ ...x, [l.id]: e.target.value }))} className="w-24" />
        </FormField>
      ))}
      <FormField label="Reason" htmlFor="ret-reason"><Input id="ret-reason" value={reason} onChange={(e) => setReason(e.target.value)} /></FormField>
      <ActionButton size="sm" permission="sales.invoice.manage" disabled={reason.trim().length < 3 || !Object.values(qty).some((v) => Number(v) > 0)}
        onAction={() => m.mutateAsync().catch(() => {})}>Record (holds the invoice)</ActionButton>
      <ErrorMessage error={m.error} />
    </section>
  )
}
