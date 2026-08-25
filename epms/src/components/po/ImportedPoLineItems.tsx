import { Plus, Trash2 } from 'lucide-react'
import { formatAmount, formatDate, formatUnitPrice } from '@/lib/utils'
import { lineTotalOf } from '@/lib/importedPoEdit'

/** One line of an NC-imported PO as the buyer-detail form sees it.
 *
 *  Two kinds share this table. On an NC-sourced line everything except
 *  supplierItemId and sample is display-only: the ERP owns those values and the
 *  endpoint cannot write them. On a buyer-added line — a one-off charge such as
 *  a mould/tooling quote the supplier wants itemised, which NC has no way to
 *  carry — every field is the buyer's, and the line can be removed again. */
export interface ImportedPoLine {
  id: string
  /** False on a buyer-added line. Decides which cells are inputs. */
  ncSourced: boolean
  description: string
  materialId?: string | null
  qty: number
  unit: string
  unitPrice: number
  lineTotal: number
  supplierItemId: string
  sample: string
  // ERP-synced per-line arrival date. Display-only — there is no field on
  // PoImportedDetailsUpdate to write it back, and an added line has none.
  plannedArrivalDate?: string | null
}

interface ImportedPoLineItemsProps {
  items: ImportedPoLine[]
  onChange: (items: ImportedPoLine[]) => void
  currency: string
  /** Whether rows may be added, edited or removed at all. */
  canAddLines?: boolean
  /** Locked because an invoice already points at this PO. */
  locked?: boolean
}

const CELL_INPUT =
  'h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm ' +
  'focus:outline-none focus:ring-1 focus:ring-primary-600 ' +
  'disabled:bg-neutral-50 disabled:text-neutral-400'

let addedSeq = 0

/** Line table for the imported-PO buyer-detail form.
 *
 *  Intentionally NOT PrLineItems: that component serves four pages and carries
 *  pickers, reordering and validation shaped around a document UniOps owns
 *  outright. Here most rows belong to the ERP and only some are the buyer's.
 */
export function ImportedPoLineItems({
  items, onChange, currency, canAddLines = false, locked = false,
}: ImportedPoLineItemsProps) {
  const editable = canAddLines && !locked

  const update = (index: number, patch: Partial<ImportedPoLine>) => {
    onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  const addRow = () => {
    // A client-side id so React can key the row and the buyer can edit it
    // before it exists server-side. Stripped from the payload: an entry with no
    // id is what tells the endpoint to create the line.
    addedSeq += 1
    onChange([...items, {
      id: `new-${addedSeq}`, ncSourced: false, description: '',
      materialId: null, qty: 1, unit: 'EA', unitPrice: 0, lineTotal: 0,
      supplierItemId: '', sample: '', plannedArrivalDate: null,
    }])
  }

  const removeRow = (index: number) => {
    onChange(items.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-neutral-200">
        <table className="w-full min-w-[980px] text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50 text-left text-xs text-neutral-500">
              <th className="w-10 px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">Description</th>
              <th className="w-32 px-3 py-2 font-medium">Material ID</th>
              <th className="w-24 px-3 py-2 text-right font-medium">Qty</th>
              <th className="w-20 px-3 py-2 font-medium">Unit</th>
              <th className="w-28 px-3 py-2 text-right font-medium">Unit Price</th>
              <th className="w-28 px-3 py-2 text-right font-medium">Line Total</th>
              <th className="w-28 px-3 py-2 font-medium">Delivery Date</th>
              <th className="w-40 px-3 py-2 font-medium">Supplier Item ID</th>
              <th className="w-32 px-3 py-2 font-medium">Sample (g or ea)</th>
              <th className="w-10 px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => (
              <tr key={item.id} className="border-b border-neutral-100 last:border-0">
                <td className="px-3 py-2 text-neutral-400">{i + 1}</td>
                <td className="px-3 py-2 text-neutral-900">
                  {item.ncSourced ? item.description : (
                    <input
                      type="text"
                      value={item.description}
                      onChange={(e) => update(i, { description: e.target.value })}
                      placeholder="e.g. Mould tooling"
                      aria-label={`Description for added line ${i + 1}`}
                      maxLength={500}
                      disabled={locked}
                      className={CELL_INPUT}
                    />
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-neutral-500">
                  {item.ncSourced ? (item.materialId || '—') : (
                    // A buyer-added line has no material code by design — that
                    // is what keeps a one-off charge out of MRP's supply.
                    <span className="rounded bg-neutral-100 px-1.5 py-0.5 font-sans text-[11px] text-neutral-600">
                      Added
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-neutral-700">
                  {item.ncSourced ? item.qty : (
                    <input
                      type="number" step="any" min="0"
                      value={item.qty}
                      onChange={(e) => update(i, { qty: Number(e.target.value) })}
                      aria-label={`Quantity for added line ${i + 1}`}
                      disabled={locked}
                      className={`${CELL_INPUT} text-right font-mono`}
                    />
                  )}
                </td>
                <td className="px-3 py-2 text-neutral-700">
                  {item.ncSourced ? item.unit : (
                    <input
                      type="text"
                      value={item.unit}
                      onChange={(e) => update(i, { unit: e.target.value })}
                      aria-label={`Unit for added line ${i + 1}`}
                      maxLength={30}
                      disabled={locked}
                      className={CELL_INPUT}
                    />
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-neutral-700">
                  {item.ncSourced ? formatUnitPrice(item.unitPrice, currency) : (
                    <input
                      type="number" step="any"
                      value={item.unitPrice}
                      onChange={(e) => update(i, { unitPrice: Number(e.target.value) })}
                      aria-label={`Unit price for added line ${i + 1}`}
                      disabled={locked}
                      className={`${CELL_INPUT} text-right font-mono`}
                    />
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-neutral-900">
                  {formatAmount(
                    item.ncSourced ? item.lineTotal : lineTotalOf(item),
                    currency,
                  )}
                </td>
                <td className="px-3 py-2 text-neutral-500">
                  {item.plannedArrivalDate ? formatDate(item.plannedArrivalDate) : '—'}
                </td>
                <td className="px-3 py-2">
                  <input
                    type="text"
                    value={item.supplierItemId}
                    onChange={(e) => update(i, { supplierItemId: e.target.value })}
                    placeholder="SKU / catalog #"
                    aria-label={`Supplier item ID for line ${i + 1}`}
                    maxLength={100}
                    className={`${CELL_INPUT} font-mono`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    type="text"
                    value={item.sample}
                    onChange={(e) => update(i, { sample: e.target.value })}
                    placeholder="e.g. 500 g"
                    aria-label={`Sample requirement for line ${i + 1}`}
                    maxLength={100}
                    className={CELL_INPUT}
                  />
                </td>
                <td className="px-3 py-2 text-right">
                  {!item.ncSourced && editable && (
                    <button
                      type="button"
                      onClick={() => removeRow(i)}
                      aria-label={`Remove added line ${i + 1}`}
                      className="text-neutral-400 hover:text-danger-600"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editable && (
        <button
          type="button"
          onClick={addRow}
          className="inline-flex items-center gap-1 text-sm text-primary-600 hover:text-primary-700"
        >
          <Plus className="h-4 w-4" />
          Add Row
        </button>
      )}
      {canAddLines && locked && (
        <p className="text-xs text-neutral-500">
          Added lines are locked — an invoice has been raised against this PO.
        </p>
      )}
    </div>
  )
}
