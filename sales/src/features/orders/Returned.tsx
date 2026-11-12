import { z } from 'zod'
import { MasterDetailForm, readColumn, textColumn, type EditableColumn, type FieldSpec } from '@/platform-ui'
import { lineNumbers, setReturned, type OrderDetail } from './ordersApi'

const errorMessages: Record<string, string> = {
  'returned.invoice_sent': 'The invoice is sent: record a later return with Record a return.',
  'returned.more_than_shipped': 'A line cannot have more returned than shipped.',
  'returned.part_case': 'The invoice bills whole cases: what is delivered must be a whole number of cases.',
  'returned.invoice_line': 'This line is not on the invoice: change it on the invoice.',
  'order.cancelled': 'The order is cancelled.',
  'auth.forbidden': 'You do not have permission to manage invoices.',
}

type ReturnLine = { id: number; line_no: number; item: string; shipped: number; returned: string }

const delivered = (l: ReturnLine) => {
  const n = Number(l.returned.trim() === '' ? 0 : l.returned)
  return Number.isInteger(n) ? l.shipped - n : null
}

const columns: EditableColumn<ReturnLine>[] = [
  readColumn<ReturnLine>('item', 'Item', (l) => l.item),
  readColumn<ReturnLine>('shipped', 'Shipped EA', (l) => l.shipped),
  textColumn<ReturnLine>('returned', 'Returned EA', { label: (l) => `Returned EA line ${l.line_no}`, get: (l) => l.returned, set: (v) => ({ returned: v }) }),
  readColumn<ReturnLine>('delivered', 'Delivered EA', (l) => delivered(l) ?? '—'),
]

const problem = (l: ReturnLine) => {
  const t = l.returned.trim()
  if (t === '') return null
  const n = Number(t)
  if (!Number.isInteger(n) || n < 0) return 'a whole number of EA, 0 or more'
  return n > l.shipped ? `at most the ${l.shipped} EA shipped` : null
}

const schema = z.object({ reason: z.string().trim().min(3, 'Say why, in a few words').max(500) })
const fields: FieldSpec[] = [
  { name: 'reason', label: 'Why (returned or refused by the customer)', type: 'textarea', required: true,
    hint: 'Kept in the order history with each change.' },
]

/** What the customer returned (or refused) per shipped line, before the invoice goes (user
 *  2026-11-12): delivered = shipped − returned, and the invoice bills the delivered. */
export function ReturnedForm({ o, onSaved, onCancel }: { o: OrderDetail; onSaved: (d: OrderDetail) => void; onCancel: () => void }) {
  const numbers = lineNumbers(o)
  const shipped = o.lines.filter((ln) => !ln.removed && ln.shipped_ea != null)
  return (
    <MasterDetailForm<ReturnLine>
      schema={schema} fields={fields} defaultValues={{ reason: '' }}
      lines={{ label: 'Returned', columns, rowKey: (l) => l.id, problem, minRows: 0,
        initial: shipped.map((ln) => ({ id: ln.id, line_no: numbers.get(ln.line_no) ?? ln.line_no, item: ln.item_code ?? 'unmatched',
          shipped: ln.shipped_ea ?? 0, returned: String(ln.returned_ea ?? 0) })) }}
      submitLabel="Save returned" errorMessages={errorMessages} onCancel={onCancel}
      onSubmit={async (v, lines) => onSaved(await setReturned(o.id, {
        reason: String(v.reason ?? '').trim(),
        lines: lines.map((l) => ({ id: l.id, returned_ea: l.returned.trim() === '' ? 0 : Number(l.returned) })) }))}
    />
  )
}
