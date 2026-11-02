import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, Button, DateInput, Dialog, ErrorMessage, FormField, MasterDetailForm, Skeleton, numberColumn, queryKeys, readColumn,
  todayDate, useCan, type FieldSpec,
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

const confirmSchema = z.object({ confirmed_date: z.string().min(1, 'Required') })
const confirmFields: FieldSpec[] = [{ name: 'confirmed_date', label: 'Delivered on (the invoice date)', type: 'date', required: true }]

/** One order: the delivered EA per line (≤ shipped) and the date — the standard
 *  form, in the order's sheet; read only without sales.invoice.manage. */
function ConfirmOne({ o, close }: { o: OrderRead; close: () => void }) {
  const qc = useQueryClient()
  const can = useCan(MANAGE)
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id) })
  if (q.isLoading || !q.data) return <Skeleton className="h-20 w-full" />
  return (
    <MasterDetailForm<Delivered>
      schema={confirmSchema} fields={confirmFields} defaultValues={{ confirmed_date: todayDate() }} readOnly={!can}
      lines={{ label: 'Delivered quantities', columns: deliveredColumns, rowKey: (d) => d.line.id, problem: deliveredProblem,
        initial: q.data.lines.filter((l) => l.shipped_ea != null).map((line) => ({ line, delivered: String(line.shipped_ea) })) }}
      submitLabel="Confirm delivery"
      onSubmit={async (v, lines) => {
        await confirmDelivery({ order_id: o.id, confirmed_date: String(v.confirmed_date),
          lines: lines.map((d) => ({ order_line_id: d.line.id, delivered_qty: Number(d.delivered) })) })
        await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
        await qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) })
        close()
      }}
    />
  )
}

/** All shown, delivered in full, on one date (FR-OB-31: in bulk) — asked in a dialog. */
function ConfirmShown({ rows, refresh }: { rows: OrderRead[]; refresh: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState(() => todayDate())
  const [result, setResult] = useState<ConfirmResult[] | null>(null)
  const m = useMutation({
    mutationFn: () => confirmBulk(rows.map((r) => r.id), date),
    onSuccess: async (r) => { setResult(r); setOpen(false); await refresh() },
  })
  const failed = result?.filter((r) => !r.ok) ?? []
  return (
    <>
      {result && <p role="status" className="text-xs">{result.length - failed.length} confirmed{failed.length ? `, ${failed.length} not: ${failed.map((f) => `#${f.order_id} ${f.error}`).join('; ')}` : ''}</p>}
      <ActionButton size="sm" permission={MANAGE} disabled={rows.length === 0} onAction={() => setOpen(true)}>
        Confirm all shown ({rows.length})…
      </ActionButton>
      <Dialog open={open} onOpenChange={setOpen} title={`Confirm ${rows.length} deliveries`}
        description="Each order shown, delivered in full (its shipped quantity), on this date — the invoice date."
        footer={<>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <ActionButton permission={MANAGE} disabled={!date} onAction={() => m.mutateAsync().catch(() => {})}>Confirm</ActionButton>
        </>}>
        <FormField label="Delivered on" htmlFor="bulk-date"><DateInput id="bulk-date" value={date} onChange={(e) => setDate(e.target.value)} /></FormField>
        <ErrorMessage error={m.error} />
      </Dialog>
    </>
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
