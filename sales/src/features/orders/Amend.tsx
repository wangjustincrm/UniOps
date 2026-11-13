import { z } from 'zod'
import { Autocomplete, MasterDetailForm, readColumn, textColumn, type EditableColumn, type FieldSpec } from '@/platform-ui'
import { itemOptions } from '@/features/configuration/lookups'
import { CARRIERS, amendOrder, isCarrier, lineNumbers, type OrderDetail } from './ordersApi'

const errorMessages: Record<string, string> = {
  'order.amend': 'This order can no longer be amended: the warehouse has shipped it, or it is not waiting for its 940.',
  'order.amend_source': "A Shopify order's items come from the store: fix the item mapping instead.",
  'order.amend_line': 'A line could not be changed: check its item and quantity.',
  'order.carrier': 'Choose how it ships: Purolator or LTL.',
  'send940.not_eligible': 'Amended. The order is not ready to send yet: it goes when it qualifies.',
  'auth.forbidden': 'You do not have permission to change orders.',
}

type AmendLine = { id: number; line_no: number; item_code: string; qty: string; unit: string }

const columns: EditableColumn<AmendLine>[] = [
  readColumn<AmendLine>('line', '#', (l) => l.line_no),
  { id: 'item', header: 'Item', cell: ({ row, update, invalid }) => (
    <Autocomplete aria-label={`Item line ${row.line_no}`} aria-invalid={invalid || undefined} value={row.item_code || null}
      onChange={(v) => update({ item_code: v ?? '' })} loadOptions={itemOptions} />
  ) },
  textColumn<AmendLine>('qty', 'Quantity', { label: (l) => `Quantity line ${l.line_no}`, get: (l) => l.qty, set: (v) => ({ qty: v }) }),
  readColumn<AmendLine>('unit', 'Unit', (l) => l.unit),
]

const problem = (l: AmendLine) => {
  if (!l.item_code) return 'choose an item'
  const n = Number(l.qty)
  return Number.isFinite(n) && n > 0 ? null : 'a quantity above 0'
}

const schema = z.object({ reason: z.string().trim().min(3, 'Say what was wrong, in a few words').max(500), carrier: z.string().nullable().optional() })
const carrierField: FieldSpec = { name: 'carrier', label: 'Ships by', type: 'segmented', options: CARRIERS,
  hint: 'A manual order: Purolator / Purolator Express (a parcel, like a Shopify order) or LTL (a pallet shipment, like a retailer order).' }
const fields: FieldSpec[] = [
  { name: 'reason', label: 'What was wrong', type: 'textarea', required: true,
    hint: 'Kept in the order history. A 940 already sent is Replaced by a new one; a rejected or timed-out one is sent again; '
      + 'a Pending order goes when it qualifies. A line taken off the list is taken off the order.' },
]

/** An order sent wrong, corrected and sent again (user 2026-11-13): until the warehouse ships it. */
export function AmendForm({ o, onSaved, onCancel }: { o: OrderDetail; onSaved: () => void; onCancel: () => void }) {
  const numbers = lineNumbers(o)
  const open = o.lines.filter((ln) => !ln.removed)
  return (
    <MasterDetailForm<AmendLine>
      schema={schema} fields={o.source_type === 'MANUAL' ? [carrierField, ...fields] : fields}
      defaultValues={{ reason: '', carrier: o.shipping_method ?? '' }}
      lines={{ label: 'Order lines', columns, rowKey: (l) => l.id, problem, removable: true,
        initial: open.map((ln) => ({ id: ln.id, line_no: numbers.get(ln.line_no) ?? ln.line_no, item_code: ln.item_code ?? '',
          qty: String(Number(ln.ordered_qty)), unit: ln.order_unit ?? 'EA' })) }}
      submitLabel="Amend and send again" errorMessages={errorMessages} onCancel={onCancel}
      onSubmit={async (v, lines) => {
        const kept = new Set(lines.map((l) => l.id))
        const carrier = isCarrier(v.carrier) ? v.carrier : null
        await amendOrder(o.id, {
          reason: String(v.reason ?? '').trim(),
          ...(o.source_type === 'MANUAL' && carrier && carrier !== o.shipping_method ? { carrier } : {}),
          lines: [
            ...lines.map((l) => ({ id: l.id, item_code: l.item_code, ordered_qty: l.qty.trim() })),
            ...open.filter((ln) => !kept.has(ln.id)).map((ln) => ({ id: ln.id, removed: true })),
          ],
        })
        onSaved()
      }}
    />
  )
}
