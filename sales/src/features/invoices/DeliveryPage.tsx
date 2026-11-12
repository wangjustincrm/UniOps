import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, Button, DateInput, Dialog, ErrorMessage, FormField, MasterDetailForm, Skeleton, numberColumn, queryKeys, readColumn,
  todayDate, useCan, type FieldSpec, useNotices,
} from '@/platform-ui'
import { useTestRunRefusal } from '@/features/common/testRun'
import { OrderList } from '@/features/orders/OrdersPage'
import { fetchOrder, ordersClient, type LineRead, type OrderRead } from '@/features/orders/ordersApi'
import { confirmBulk, confirmDelivery, invoicesClient, type ConfirmResult } from './invoicesApi'

const OPERATE = 'sales.order.operate'
const MANAGE = 'sales.invoice.manage'  // sending is invoice work: Confirm and send needs both
const WAITING = [{ field: 'to_confirm', op: 'eq' as const, value: 'true' }]

// What the customer returned (or refused) per line; delivered = shipped − returned, which the invoice bills (user 2026-11-12).
type Delivered = { line: LineRead; returned: string }
const shippedOf = (d: Delivered) => d.line.shipped_ea ?? 0
const deliveredOf = (d: Delivered) => shippedOf(d) - Number(d.returned || 0)

const deliveredColumns = [
  readColumn<Delivered>('item', 'Item', (d) => d.line.item_code),
  readColumn<Delivered>('shipped', 'Shipped EA', (d) => d.line.shipped_ea),
  numberColumn<Delivered>('returned', 'Returned EA', { label: (d) => `Returned ${d.line.item_code}`, get: (d) => d.returned,
    set: (v) => ({ returned: v }), min: 0, max: (d) => d.line.shipped_ea ?? undefined }),
  readColumn<Delivered>('delivered', 'Delivered EA', (d) => (Number.isInteger(deliveredOf(d)) ? deliveredOf(d) : '—')),
]

function deliveredProblem(d: Delivered): string | null {
  const n = Number(d.returned || 0)
  return Number.isInteger(n) && n >= 0 && n <= shippedOf(d) ? null : `0 … ${d.line.shipped_ea} EA (what shipped)`
}

const confirmSchema = z.object({ confirmed_date: z.string().min(1, 'Required') })
const confirmFields: FieldSpec[] = [{ name: 'confirmed_date', label: 'Delivered on (the invoice date)', type: 'date', required: true }]

/** One order: what the customer returned per line (delivered = shipped − returned) and the date — the standard
 *  form, in the order's sheet; read only without sales.order.operate. */
function ConfirmOne({ o, close }: { o: OrderRead; close: () => void }) {
  const qc = useQueryClient()
  const can = useCan(OPERATE)
  const canSend = useCan(MANAGE)
  const refused = useTestRunRefusal()
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id) })
  if (q.isLoading || !q.data) return <Skeleton className="h-20 w-full" />
  const save = async (v: Record<string, unknown>, lines: Delivered[], send: boolean) => {
    await confirmDelivery({ order_id: o.id, confirmed_date: String(v.confirmed_date), ...(send ? { send } : {}),
      lines: lines.map((d) => ({ order_line_id: d.line.id, delivered_qty: deliveredOf(d) })) }).catch(refused)
    await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
    await qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) })
    close()
  }
  const today = todayDate()
  const due = o.delivery_due_date && o.delivery_due_date <= today ? o.delivery_due_date : today
  return (
    <MasterDetailForm<Delivered>
      schema={confirmSchema} fields={confirmFields} defaultValues={{ confirmed_date: due }} readOnly={!can}
      lines={{ label: 'Returned and delivered', columns: deliveredColumns, rowKey: (d) => d.line.id, problem: deliveredProblem,
        initial: q.data.lines.filter((l) => (l.shipped_ea ?? l.shipped_shadow_ea) != null)
          .map((line) => ({ line: { ...line, shipped_ea: line.shipped_ea ?? line.shipped_shadow_ea }, returned: String(line.returned_ea ?? 0) })) }}
      submitLabel="Confirm delivery"
      alsoSubmit={canSend ? { label: 'Confirm and send', onSubmit: (v, lines) => save(v, lines, true) } : undefined}
      onSubmit={(v, lines) => save(v, lines, false)}
    />
  )
}

/** All shown, delivered in full (FR-OB-31: in bulk): each on its due date, or on one date given here. */
function ConfirmShown({ rows, refresh }: { rows: OrderRead[]; refresh: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState('')
  const canSend = useCan(MANAGE)
  const notices = useNotices()
  const m = useMutation({
    mutationFn: (send: boolean) => confirmBulk(rows.map((r) => r.id), date || null, send),
    onSuccess: async (r: ConfirmResult[]) => {
      // The outcome in the list's status row: a warning while some were not confirmed.
      const failed = r.filter((x) => !x.ok)
      const sent = r.filter((x) => x.sent).length
      notices.notify({ key: 'confirm', tone: failed.length ? 'warning' : 'success',
        text: `${r.length - failed.length} confirmed${sent ? `, ${sent} sent` : ''}${failed.length ? `, ${failed.length} not: ${failed.map((f) => `#${f.order_id} ${f.error}`).join('; ')}` : ''}` })
      setOpen(false)
      await refresh()
    },
  })
  return (
    <>
      <ActionButton size="sm" permission={OPERATE} disabled={rows.length === 0} onAction={() => setOpen(true)}>
        Confirm all shown ({rows.length})…
      </ActionButton>
      <Dialog open={open} onOpenChange={setOpen} title={`Confirm ${rows.length} deliveries`}
        description="Each order shown, delivered in full (its shipped quantity). Leave the date empty to use each order's Delivery due date. The date becomes the invoice date."
        footer={<>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          {canSend && <ActionButton variant="secondary" permission={OPERATE} onAction={() => m.mutateAsync(true).catch(() => {})}>Confirm and send</ActionButton>}
          <ActionButton permission={OPERATE} onAction={() => m.mutateAsync(false).catch(() => {})}>Confirm</ActionButton>
        </>}>
        <FormField label="Delivered on (optional)" htmlFor="bulk-date"><DateInput id="bulk-date" value={date} onChange={(e) => setDate(e.target.value)} /></FormField>
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
      description="Shipped orders whose delivery is due: the requested date, or do-not-deliver-after for Walmart. No carrier reports the delivery, so it defaults to the due date. Change it if you know better: it becomes the invoice date."
      showDue
      emptyTitle="Nothing waits for a delivery confirmation"
      toolbar={(ctx) => <ConfirmShown rows={ctx.rows} refresh={ctx.refresh} />}
      detailTabs={(o, close) => [{ id: 'confirm', label: 'Confirm delivery', render: () => <ConfirmOne o={o} close={close} /> }]}
    />
  )
}
