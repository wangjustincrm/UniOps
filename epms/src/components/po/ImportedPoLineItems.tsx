import { formatAmount } from '@/lib/utils'

/** One line of an NC-imported PO as the buyer-detail form sees it.
 *  Everything except supplierItemId and sample is display-only: NC owns those
 *  values and the backend endpoint cannot write them. */
export interface ImportedPoLine {
  id: string
  description: string
  materialId?: string | null
  qty: number
  unit: string
  unitPrice: number
  lineTotal: number
  supplierItemId: string
  sample: string
}

interface ImportedPoLineItemsProps {
  items: ImportedPoLine[]
  onChange: (items: ImportedPoLine[]) => void
  currency: string
}

/** Line table for the imported-PO buyer-detail form.
 *
 *  Intentionally NOT PrLineItems: that component serves four pages and carries
 *  pickers, reordering, add/remove and validation that do not apply to a
 *  mirrored order whose quantities and prices come from NC.
 */
export function ImportedPoLineItems({ items, onChange, currency }: ImportedPoLineItemsProps) {
  const update = (index: number, patch: Partial<ImportedPoLine>) => {
    onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200">
      <table className="w-full min-w-[880px] text-sm">
        <thead>
          <tr className="border-b border-neutral-200 bg-neutral-50 text-left text-xs text-neutral-500">
            <th className="w-10 px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Description</th>
            <th className="w-32 px-3 py-2 font-medium">Material ID</th>
            <th className="w-24 px-3 py-2 text-right font-medium">Qty</th>
            <th className="w-20 px-3 py-2 font-medium">Unit</th>
            <th className="w-28 px-3 py-2 text-right font-medium">Unit Price</th>
            <th className="w-28 px-3 py-2 text-right font-medium">Line Total</th>
            <th className="w-40 px-3 py-2 font-medium">Supplier Item ID</th>
            <th className="w-32 px-3 py-2 font-medium">Sample (g or ea)</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={item.id} className="border-b border-neutral-100 last:border-0">
              <td className="px-3 py-2 text-neutral-400">{i + 1}</td>
              <td className="px-3 py-2 text-neutral-900">{item.description}</td>
              <td className="px-3 py-2 font-mono text-xs text-neutral-500">
                {item.materialId || '—'}
              </td>
              <td className="px-3 py-2 text-right font-mono text-neutral-700">{item.qty}</td>
              <td className="px-3 py-2 text-neutral-700">{item.unit}</td>
              <td className="px-3 py-2 text-right font-mono text-neutral-700">
                {formatAmount(item.unitPrice, currency)}
              </td>
              <td className="px-3 py-2 text-right font-mono text-neutral-900">
                {formatAmount(item.lineTotal, currency)}
              </td>
              <td className="px-3 py-2">
                <input
                  type="text"
                  value={item.supplierItemId}
                  onChange={(e) => update(i, { supplierItemId: e.target.value })}
                  placeholder="SKU / catalog #"
                  aria-label={`Supplier item ID for line ${i + 1}`}
                  className="h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary-600"
                />
              </td>
              <td className="px-3 py-2">
                <input
                  type="text"
                  value={item.sample}
                  onChange={(e) => update(i, { sample: e.target.value })}
                  placeholder="e.g. 500 g"
                  aria-label={`Sample requirement for line ${i + 1}`}
                  className="h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600"
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
