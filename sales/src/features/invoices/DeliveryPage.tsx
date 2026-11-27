import { readMoved } from '@/features/common/moved'
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, MasterDetailForm, numberColumn, readColumn, todayDate, useCan, type ActionDone, type FieldSpec, useRecord, QueryState, FormDialog, SchemaForm, versionOf,
} from '@/platform-ui'
import { useTestRunRefusal } from '@/features/common/testRun'
import { OrderList } from '@/features/orders/OrdersPage'
import { DELIVERY_ACTIONS } from '@/features/orders/orderActions'
import { fetchOrder, ordersClient, type LineRead, type OrderRead } from '@/features/orders/ordersApi'
import { confirmBulk, confirmDelivery, type ConfirmResult } from './invoicesApi'
import { EMPTY_TEXT } from '@/features/help/pageHelp'

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
  const q = useRecord(ordersClient, o.id, fetchOrder, { errors: 'inline' })
  if (!q.data) return <QueryState q={q}>{() => null}</QueryState>
  const seen = q.data.version
  const save = async (v: Record<string, unknown>, lines: Delivered[], send: boolean, version: number) => {
    await confirmDelivery({ order_id: o.id, version, confirmed_date: String(v.confirmed_date), ...(send ? { send } : {}),
      lines: lines.map((d) => ({ order_line_id: d.line.id, delivered_qty: deliveredOf(d) })) }).catch(refused)
    await readMoved(qc, o.id)
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
      alsoSubmit={canSend ? { label: 'Confirm and send', onSubmit: (v, lines, edit) => save(v, lines, true, versionOf(edit)) } : undefined}
      version={seen}
      onSubmit={(v, lines, edit) => save(v, lines, false, versionOf(edit))}
    />
  )
}

const bulkSchema = z.object({ date: z.string().nullable().optional() })
const bulkFields: FieldSpec[] = [{ name: 'date', label: 'Delivered on (optional)', type: 'date' }]

/** All shown, delivered in full (FR-OB-31: in bulk): each on its due date, or on one date given here. */
function ConfirmShown({ rows, refresh }: { rows: OrderRead[]; refresh: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const canSend = useCan(MANAGE)
  // The outcome in the list's status row: a warning while some were not confirmed; the dialog closes.
  const confirmed = {
    done: (res: unknown): ActionDone => {
      const r = res as ConfirmResult[]
      const failed = r.filter((x) => !x.ok)
      const sent = r.filter((x) => x.sent).length
      return { key: 'confirm', tone: failed.length ? 'warning' : 'success',
        text: `${r.length - failed.length} confirmed${sent ? `, ${sent} sent` : ''}${failed.length ? `, ${failed.length} not: ${failed.map((f) => `#${f.order_id} ${f.error}`).join('; ')}` : ''}` }
    },
    after: async () => { setOpen(false); await refresh() },
  }
  const run = (send: boolean) => (v: Record<string, unknown>) => confirmBulk(rows, (v.date as string | null) || null, send)
  return (
    <>
      <ActionButton size="sm" permission={OPERATE} disabled={rows.length === 0} disabledReason="No order is shown to confirm." onAction={() => setOpen(true)}>
        Confirm all shown ({rows.length})…
      </ActionButton>
      <FormDialog open={open} onOpenChange={setOpen} title={`Confirm ${rows.length} deliveries`}
        description="Each order shown, delivered in full (its shipped quantity). Leave the date empty to use each order's Delivery due date. The date becomes the invoice date.">
        <SchemaForm schema={bulkSchema} fields={bulkFields} defaultValues={{ date: '' }} submitLabel="Confirm" onCancel={() => setOpen(false)}
          {...confirmed} onSubmit={run(false)} alsoSubmit={canSend ? { label: 'Confirm and send', onSubmit: run(true) } : undefined} />
      </FormDialog>
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
      
      showDue
      actions={DELIVERY_ACTIONS}
      emptyTitle="Nothing waits for a delivery confirmation"
      emptyText={EMPTY_TEXT.delivery}
      help="page:delivery"
      toolbar={(ctx) => <ConfirmShown rows={ctx.rows} refresh={ctx.refresh} />}
      detailTabs={(o, close) => [{ id: 'confirm', label: 'Confirm delivery', render: () => <ConfirmOne o={o} close={close} /> }]}
    />
  )
}
