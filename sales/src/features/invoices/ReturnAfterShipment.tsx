import { useState } from 'react'
import { orderRef } from '@/features/common/labels'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, Dialog, MasterDetailForm, numberColumn, queryKeys, readColumn, useCan, type FieldSpec,
} from '@/platform-ui'
import { fetchOrder, ordersClient, type LineRead, type OrderRead } from '@/features/orders/ordersApi'
import { recordReturn } from './invoicesApi'

const MANAGE = 'sales.invoice.manage'
const KINDS = [{ value: 'Returned', label: 'Returned' }, { value: 'Cancelled after shipment', label: 'Cancelled after shipment' }]
const schema = z.object({
  kind: z.enum(['Returned', 'Cancelled after shipment']),
  reason: z.string().trim().min(3, 'Say why (3 characters or more)').max(2000),
})
const fields: FieldSpec[] = [
  { name: 'kind', label: 'What happened', type: 'select', required: true, options: KINDS },
  { name: 'reason', label: 'Reason', type: 'textarea', required: true },
]
type Back = { line: LineRead; qty: string }
const columns = [
  readColumn<Back>('item', 'Item', (b) => b.line.item_code),
  readColumn<Back>('shipped', 'Shipped EA', (b) => b.line.shipped_ea),
  numberColumn<Back>('qty', 'EA', { label: (b) => `EA ${b.line.item_code}`, get: (b) => b.qty, set: (v) => ({ qty: v }), min: 0,
    max: (b) => b.line.shipped_ea ?? undefined }),
]
const problem = (b: Back) => {
  const n = Number(b.qty || 0)
  return Number.isInteger(n) && n >= 0 && n <= (b.line.shipped_ea ?? 0) ? null : `0 … ${b.line.shipped_ea} EA`
}

/** FR-OB-19: a retailer order cancelled or returned after shipment — recorded
 *  with quantities and reason (the standard form, in a dialog); the invoice
 *  holds and a task is raised. */
export function ReturnAfterShipment({ o }: { o: OrderRead }) {
  const can = useCan(MANAGE)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const shipped = o.fulfillment_status === 'Shipped' || o.fulfillment_status === 'On Hold'
  const q = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id), enabled: open })
  if (!shipped || !can || o.source_type !== 'EDI_850') return null
  return (
    <>
      <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setOpen(true)}>Record a return or cancellation…</ActionButton>
      <Dialog open={open} onOpenChange={setOpen} title={`Order ${orderRef(o)}: return or cancellation after shipment`} className="max-w-2xl">
        {open && q.data && (
          <MasterDetailForm<Back>
            schema={schema} fields={fields} defaultValues={{ kind: 'Returned', reason: '' }}
            lines={{ label: 'Quantities back', columns, problem, rowKey: (b) => b.line.id,
              initial: q.data.lines.filter((l) => l.shipped_ea).map((line) => ({ line, qty: '' })) }}
            blocked={(st) => (st.lines.some((b) => Number(b.qty) > 0) ? null : 'Enter the EA of at least one line.')}
            submitLabel="Record (holds the invoice)" onCancel={() => setOpen(false)}
            onSubmit={async (v, lines) => {
              await recordReturn({ order_id: o.id, kind: v.kind as 'Returned' | 'Cancelled after shipment', reason: String(v.reason),
                lines: lines.filter((b) => Number(b.qty) > 0).map((b) => ({ order_line_id: b.line.id, qty_ea: Number(b.qty) })) })
              setOpen(false)
              await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) })
            }}
          />
        )}
      </Dialog>
    </>
  )
}
