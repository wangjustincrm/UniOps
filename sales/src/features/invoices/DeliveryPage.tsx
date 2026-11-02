import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionButton, DateInput, ErrorMessage, FormField, NumberInput, Skeleton, queryKeys, todayDate } from '@/platform-ui'
import { OrderList } from '@/features/orders/OrdersPage'
import { fetchOrder, ordersClient, type OrderRead } from '@/features/orders/ordersApi'
import { confirmBulk, confirmDelivery, invoicesClient, type ConfirmResult } from './invoicesApi'

const MANAGE = 'sales.invoice.manage'
const WAITING = [{ field: 'invoice_status', op: 'eq' as const, value: 'Waiting for Delivery Confirmation' }]

function ConfirmOne({ o, close }: { o: OrderRead; close: () => void }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id) })
  const [date, setDate] = useState(() => todayDate())
  const [qty, setQty] = useState<Record<number, string>>({})
  const m = useMutation({
    mutationFn: () => confirmDelivery({
      order_id: o.id, confirmed_date: date,
      lines: (q.data?.lines ?? []).filter((l) => l.shipped_ea != null)
        .map((l) => ({ order_line_id: l.id, delivered_qty: Number(qty[l.id] ?? l.shipped_ea) })),
    }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
      await qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) })
      close()
    },
  })
  if (q.isLoading || !q.data) return <Skeleton className="h-20 w-full" />
  const shipped = q.data.lines.filter((l) => l.shipped_ea != null)
  return (
    <section aria-label="Confirm delivery" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2 text-sm">
      <FormField label="Delivered on (the invoice date)" htmlFor="dlv-date"><DateInput id="dlv-date" value={date} onChange={(e) => setDate(e.target.value)} /></FormField>
      <table className="w-full text-xs">
        <thead><tr className="text-left text-neutral-500"><th>Item</th><th>Shipped EA</th><th>Delivered EA</th></tr></thead>
        <tbody>
          {shipped.map((l) => (
            <tr key={l.id} className="border-t border-neutral-100">
              <td className="py-1">{l.item_code}</td><td>{l.shipped_ea}</td>
              <td><NumberInput aria-label={`Delivered ${l.item_code}`} min={0} max={l.shipped_ea ?? undefined} value={qty[l.id] ?? String(l.shipped_ea)}
                onChange={(e) => setQty((x) => ({ ...x, [l.id]: e.target.value }))} className="w-24" /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <ActionButton size="sm" permission={MANAGE} onAction={() => m.mutateAsync().catch(() => {})}>Confirm delivery</ActionButton>
      <ErrorMessage error={m.error} />
    </section>
  )
}

function ConfirmShown({ rows, refresh }: { rows: OrderRead[]; refresh: () => Promise<void> }) {
  const [date, setDate] = useState(() => todayDate())
  const [result, setResult] = useState<ConfirmResult[] | null>(null)
  const m = useMutation({
    mutationFn: () => confirmBulk(rows.map((r) => r.id), date),
    onSuccess: async (r) => { setResult(r); await refresh() },
  })
  const failed = result?.filter((r) => !r.ok) ?? []
  return (
    <div className="flex flex-wrap items-end gap-2">
      <FormField label="Delivered on" htmlFor="bulk-date"><DateInput id="bulk-date" value={date} onChange={(e) => setDate(e.target.value)} /></FormField>
      <ActionButton size="sm" permission={MANAGE} disabled={rows.length === 0} onAction={() => m.mutateAsync().catch(() => {})}>
        Confirm all shown ({rows.length}) as delivered in full
      </ActionButton>
      {result && <p role="status" className="text-xs">{result.length - failed.length} confirmed{failed.length ? `, ${failed.length} not: ${failed.map((f) => `#${f.order_id} ${f.error}`).join('; ')}` : ''}</p>}
      <ErrorMessage error={m.error} />
    </div>
  )
}

/** Invoicing → Delivery confirmation (FR-OB-31): shipped orders whose requested
 *  date is reached. Nothing is invoiced before a person confirms; unconfirmed
 *  orders stay here and age — nothing escalates. */
export default function DeliveryPage() {
  return (
    <OrderList
      fixed={WAITING}
      title="Delivery confirmation"
      description="Shipped orders whose requested delivery date is reached (Walmart: do-not-deliver-after). Confirm what was delivered: the date becomes the invoice date."
      emptyTitle="Nothing waits for a delivery confirmation"
      toolbar={(ctx) => <ConfirmShown rows={ctx.rows} refresh={ctx.refresh} />}
      detailExtra={(o, close) => <ConfirmOne o={o} close={close} />}
    />
  )
}
