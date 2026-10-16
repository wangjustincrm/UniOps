import { CircleDot } from 'lucide-react'
import { formatAmount } from '@/lib/utils'
import { derivedLinesTotal, type DerivedPaLine } from '@/lib/paLinesFromInvoices'

// Read-only line table for a PA whose invoices were matched line by line: the
// lines are what those invoices bought, so there is nothing to tick. Shown in
// place of the PO line picker on the create and edit screens.
export function DerivedPaLinesTable({ lines, currency }: { lines: DerivedPaLine[]; currency: string }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium text-neutral-600 flex items-center gap-1.5">
        <CircleDot className="h-3.5 w-3.5" /> Line Items
        <span className="text-neutral-400 font-normal">(from the selected invoices' line matching)</span>
      </p>
      <div className="rounded-lg border border-neutral-200 overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th className="px-3 py-2 text-left font-semibold text-neutral-500 uppercase tracking-wide">Description</th>
              <th className="px-3 py-2 text-right font-semibold text-neutral-500 uppercase tracking-wide w-20">Qty</th>
              <th className="px-3 py-2 text-right font-semibold text-neutral-500 uppercase tracking-wide w-24">Unit Price</th>
              <th className="px-3 py-2 text-right font-semibold text-neutral-500 uppercase tracking-wide w-28">Line Total</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i} className="border-b border-neutral-100 last:border-0">
                <td className="px-3 py-2.5">
                  <div className="text-neutral-800 font-medium">{l.description}</div>
                  <div className="text-[10px] text-neutral-400 mt-0.5">
                    {l.po_line_id ? l.notes : `Non-PO fee · ${l.notes}`}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-neutral-500">{l.qty} {l.unit}</td>
                <td className="px-3 py-2.5 text-right font-mono text-neutral-500">{l.unit_price}</td>
                <td className="px-3 py-2.5 text-right font-mono font-semibold text-neutral-900">
                  {formatAmount(l.line_total, currency)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex items-center justify-between px-4 py-2 border-t border-neutral-200 bg-primary-50">
          <span className="text-xs text-primary-700 font-medium">{lines.length} line{lines.length !== 1 ? 's' : ''}</span>
          <span className="text-xs font-mono font-semibold text-primary-700">
            {formatAmount(derivedLinesTotal(lines), currency)}
          </span>
        </div>
      </div>
    </div>
  )
}
