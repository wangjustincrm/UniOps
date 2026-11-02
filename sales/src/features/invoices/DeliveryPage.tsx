import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, DateInput, EditableTable, ErrorMessage, FormField, Skeleton, numberColumn, queryKeys, readColumn, todayDate,
} from '@/platform-ui'
import { OrderList } from '@/features/orders/OrdersPage'
import { fetchOrder, ordersClient, type LineRead, type OrderRead } from '@/features/orders/ordersApi'
import { confirmBulk, confirmDelivery, invoicesClient, type ConfirmResult } from './invoicesApi'

const MANAGE = 'sales.invoice.manage'
const WAITING = [{ field: 'invoice_status', op: 'eq' as const, value: 'Waiting for Delivery Confirmation' }]

type Delivered = { line: LineRead; delivered: string }

const deliveredColumns = [
  readColumn<Delivered>('item', 'Item', (d) => d.line.item_code),
  readColumn<Delivered>('shipped', 'Shipped EA', (d) => d.line.shipped_ea),
  numberColumn<Delivered>('delivered', 'Delivered EA', { label: (d) => `Delivered ${d.line.item_code}`, get: (d) => d.delivered,
    set: (v) => ({ delivered: v }), min: 0, max: (d) => d.line.shipped_ea ?? undefined }),
]

function deliveredProblem(d: Delivered): string | null {
  const n = Number(d.delivered)
  return Number.isInteger(n) && n >= 0 && n <= (d.line.shipped_ea ?? 0) ? null : `0 … ${d.line.shipped_ea} EA (the shipped quantity)`
}

function ConfirmOne({ o, close }: { o: OrderRead; close: () => void }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id) })
  const [date, setDate] = useState(() => todayDate())
  const [edited, setEdited] = useState<Delivered[] | null>(null)
  const rows: Delivered[] = edited ?? (q.data?.lines ?? []).filter((l) => l.shipped_ea != null)
    .map((line) => ({ line, delivered: String(line.shipped_ea) }))
  const m = useMutation({
    mutationFn: () => confirmDelivery({
      order_id: o.id, confirmed_date: date,
      lines: rows.map((d) => ({ order_line_id: d.line.id, delivered_qty: Number(d.delivered) })),
    }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
      await qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) })
      close()
    },
  })
  if (q.isLoading || !q.data) return <Skeleton className="h-20 w-full" />
  return (
    <section aria-label="Confirm delivery" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2 text-sm">
      <FormField label="Delivered on (the invoice date)" htmlFor="dlv-date"><DateInput id="dlv-date" value={date} onChange={(e) => setDate(e.target.value)} /></FormField>
      <EditableTable<Delivered> ariaLabel="Delivered quantities" rows={rows} onChange={setEdited} columns={deliveredColumns}
        rowKey={(d) => d.line.id} errors={deliveredProblem} />
      <ActionButton size="sm" permission={MANAGE} disabled={rows.some((d) => deliveredProblem(d) !== null)}
        onAction={() => m.mutateAsync().catch(() => {})}>Confirm delivery</ActionButton>
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
