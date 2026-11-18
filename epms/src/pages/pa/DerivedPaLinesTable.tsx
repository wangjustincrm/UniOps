import { CircleDot } from 'lucide-react'
import { formatAmount, formatQty } from '@/lib/utils'
import { derivedLinesTotal, type DerivedPaLine } from '@/lib/paLinesFromInvoices'

import { TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'
// Read-only line table for a PA whose invoices were matched line by line: the
// lines are what those invoices bought, so there is nothing to tick. Shown in
// place of the PO line picker on the create and edit screens.
// `fromInvoiceLines`: an agreement PA, whose lines are the invoices' own lines
// (no PO line behind any of them, and none of them a "non-PO fee").
export function DerivedPaLinesTable({ lines, currency, fromInvoiceLines = false }: {
  lines: DerivedPaLine[]; currency: string; fromInvoiceLines?: boolean
}) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium text-fg-secondary flex items-center gap-1.5">
        <CircleDot className="h-3.5 w-3.5" /> Line Items
        <span className="text-fg-muted font-normal">
          {fromInvoiceLines ? "(from the selected invoices' lines)" : "(from the selected invoices' line matching)"}
        </span>
      </p>
      <div className="rounded-lg border border-line overflow-hidden">
        <Table density="compact">
          <THead>
            <Tr className="bg-surface-subtle">
              <Th>Description</Th>
              <Th align="right" className="w-20">Qty</Th>
              <Th align="right" className="w-24">Unit Price</Th>
              <Th align="right" className="w-28">Line Total</Th>
            </Tr>
          </THead>
          <TBody>
            {lines.map((l, i) => (
              <Tr key={i}>
                <Td>
                  <div className="text-fg font-medium">{l.description}</div>
                  <div className="text-2xs text-fg-muted mt-0.5">
                    {l.po_line_id || fromInvoiceLines ? l.notes : `Non-PO fee · ${l.notes}`}
                  </div>
                </Td>
                <Td align="right" className="font-mono text-fg-muted">{formatQty(l.qty)} {l.unit}</Td>
                <Td align="right" className="font-mono text-fg-muted">{l.unit_price}</Td>
                <Td align="right" className="font-mono font-semibold text-fg">
                  {formatAmount(l.line_total, currency)}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
        <div className="flex items-center justify-between px-4 py-2 border-t border-line bg-primary-50">
          <span className="text-xs text-primary-700 font-medium">{lines.length} line{lines.length !== 1 ? 's' : ''}</span>
          <span className="text-xs font-mono font-semibold text-primary-700">
            {formatAmount(derivedLinesTotal(lines), currency)}
          </span>
        </div>
      </div>
    </div>
  )
}
