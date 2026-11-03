import { z } from 'zod'
import {
  MasterDetailForm, formatMoney, readColumn, textColumn,
  type EditableColumn, type FieldSpec,
} from '@/platform-ui'
import { setInvoicePrices, type OrderDetail } from './ordersApi'

const errorMessages: Record<string, string> = {
  'invoice.reason_required': 'An invoice price differs from the PO: give the reason.',
  'order.invoice_exists': 'The invoice is drafted: change the price on the invoice.',
  'order.cancelled': 'The order is cancelled.',
  'auth.forbidden': 'You do not have permission to manage invoices.',
}

type PriceLine = { id: number; line_no: number; item: string; ordered: string; po_price: string | null; price: string }

// Prices compare as numbers ("48.50000" is 48.5).
const differs = (a: string | null | undefined, b: string | null | undefined) =>
  a != null && b != null && String(a).trim() !== '' && Number(a) !== Number(b)

/** The PO's price beside the line's invoice price, marked when they differ. */
export function VsPo({ price, po, currency }: { price: string | null | undefined; po: string | null; currency: string }) {
  if (!differs(price, po)) return null
  const d = Number(price) - Number(po)
  return <span className="ml-1 text-xs font-medium text-warning-700">{d > 0 ? '+' : ''}{formatMoney(d.toFixed(2), currency)} vs PO</span>
}

const columns = (currency: string): EditableColumn<PriceLine>[] => [
  readColumn<PriceLine>('item', 'Item', (l) => l.item),
  readColumn<PriceLine>('ordered', 'Ordered', (l) => l.ordered),
  readColumn<PriceLine>('po_price', 'Unit price (PO)', (l) => (l.po_price == null ? '—' : formatMoney(l.po_price, currency))),
  textColumn<PriceLine>('price', 'Invoice price', { label: (l) => `Invoice price line ${l.line_no}`, get: (l) => l.price, set: (v) => ({ price: v }) }),
  readColumn<PriceLine>('vs', '', (l) => <VsPo price={l.price} po={l.po_price} currency={currency} />),
]

const problem = (l: PriceLine) =>
  l.price.trim() !== '' && (Number.isNaN(Number(l.price)) || Number(l.price) < 0) ? 'a price of 0 or more (empty = the PO price)' : null

const schema = z.object({ reason: z.string().trim().max(500).nullable().optional() })
const fields: FieldSpec[] = [
  { name: 'reason', label: 'Reason for a price different from the PO', type: 'textarea',
    hint: 'Needed when an invoice price differs from the PO price; kept in the history.' },
]

/** The invoice price per order line before the invoice exists (per order
 *  unit, as the PO price): the draft takes it. Empty or equal to the PO = the
 *  PO price. After the draft exists, prices are changed on the invoice. */
export function InvoicePricesForm({ o, onSaved, onCancel }: { o: OrderDetail; onSaved: (d: OrderDetail) => void; onCancel: () => void }) {
  const open = o.lines.filter((ln) => !ln.removed)
  return (
    <MasterDetailForm<PriceLine>
      schema={schema} fields={fields} defaultValues={{ reason: '' }}
      lines={{ label: 'Invoice prices', columns: columns(o.currency), rowKey: (l) => l.id, problem, minRows: 0,
        initial: open.map((ln) => ({ id: ln.id, line_no: ln.line_no, item: ln.item_code ?? 'unmatched',
          ordered: `${Number(ln.ordered_qty)} ${ln.order_unit ?? '?'}`, po_price: ln.unit_price,
          price: ln.invoice_price == null ? '' : String(Number(ln.invoice_price)) })) }}
      // A price that differs from the PO needs its reason (the server refuses it without one).
      blocked={({ values, lines }) => (lines.some((l) => differs(l.price, l.po_price) || (l.po_price == null && l.price.trim() !== ''))
        && !String(values.reason ?? '').trim() ? 'An invoice price differs from the PO: give the reason.' : null)}
      submitLabel="Save prices" errorMessages={errorMessages} onCancel={onCancel}
      onSubmit={async (v, lines) => onSaved(await setInvoicePrices(o.id, {
        reason: (v.reason as string | null) ?? null,
        lines: lines.map((l) => ({ id: l.id, invoice_unit_price: l.price.trim() === '' ? null : l.price.trim() })) }))}
    />
  )
}
