import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { formatAmount } from '@/lib/utils'
import type { ApiInvoice, InvoiceLineItem, AllocationInput } from '@/services/invoices'
import type { ApiPo } from '@/services/po'

// invoiceLineId -> { poId, poLineId }
export type AllocationAssignment = Record<string, { poId: string; poLineId: string }>

interface Props {
  invoice: ApiInvoice
  pos: ApiPo[]                 // candidate POs (issued/approved, same vendor)
  onSubmit: (allocations: AllocationInput[]) => void
  submitting?: boolean
  defaultAssignments?: AllocationAssignment  // prefill (e.g. AI-recognized PO on upload)
}

export function InvoiceAllocationPanel({ invoice, pos, onSubmit, submitting, defaultAssignments }: Props) {
  const lines: InvoiceLineItem[] = invoice.line_items ?? []
  const [assign, setAssign] = useState<AllocationAssignment>(defaultAssignments ?? {})
  const [dragLineId, setDragLineId] = useState<string | null>(null)

  const currency = invoice.currency
  // Allocations are pre-tax: invoice lines and PO lines are pre-tax, so we balance
  // against the invoice's pre-tax amount. Tax is reconciled at the invoice header.
  const total = Number(invoice.amount)
  const tax = Number(invoice.tax_amount)

  const allocatedByLine = (lineId: string) => {
    const li = lines.find((l) => l.id === lineId)
    return li ? Number(li.line_total) : 0
  }

  const assignedTotal = useMemo(
    () => Object.keys(assign).reduce((s, lid) => s + allocatedByLine(lid), 0),
    [assign, lines],
  )

  // Same-amount hint: amounts that appear on BOTH sides get the same accent
  // color — a visual shortcut only, it never assigns anything.
  const amountColorMap = useMemo(() => {
    const HINT_COLORS = [
      'border-l-emerald-400', 'border-l-sky-400', 'border-l-amber-400',
      'border-l-fuchsia-400', 'border-l-indigo-400', 'border-l-rose-400',
      'border-l-teal-400', 'border-l-orange-400',
    ]
    const key = (n: number) => n.toFixed(2)
    const invAmounts = new Set(lines.map((l) => key(Number(l.line_total))))
    const poAmounts = new Set(pos.flatMap((po) => po.line_items.map((pl) => key(Number(pl.line_total)))))
    const shared = [...invAmounts].filter((a) => poAmounts.has(a) && Number(a) > 0)
    return new Map(shared.map((a, i) => [a, HINT_COLORS[i % HINT_COLORS.length]]))
  }, [lines, pos])
  const hintColor = (amount: number) => amountColorMap.get(amount.toFixed(2))
  const unallocated = total - assignedTotal
  const balanced = Math.abs(unallocated) < 0.01

  const drop = (poId: string, poLineId: string) => {
    if (!dragLineId) return
    setAssign((prev) => ({ ...prev, [dragLineId]: { poId, poLineId } }))
    setDragLineId(null)
  }

  const clearLine = (lineId: string) =>
    setAssign((prev) => { const n = { ...prev }; delete n[lineId]; return n })

  const buildAllocations = (): AllocationInput[] =>
    Object.entries(assign).map(([invoiceLineId, t]) => ({
      invoice_line_id: invoiceLineId,
      po_id: t.poId,
      po_line_id: t.poLineId,
      allocated_amount: allocatedByLine(invoiceLineId),
      allocated_tax: 0,
    }))

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-2.5">
        <div className="flex flex-col">
          <span className="text-xs text-neutral-500">Unallocated balance (pre-tax)</span>
          {tax > 0 && (
            <span className="text-[11px] text-neutral-400">
              Tax {formatAmount(tax, currency)} is handled at the invoice header, not allocated
            </span>
          )}
        </div>
        <span className={`font-mono text-sm font-semibold ${balanced ? 'text-success-600' : 'text-danger-600'}`}>
          {formatAmount(unallocated, currency)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Invoice lines (drag source) */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">Invoice lines</h4>
          <div className="flex max-h-[55vh] flex-col gap-2 overflow-y-auto pr-1">
            {lines.map((l) => {
              const a = l.id ? assign[l.id] : undefined
              return (
                <div key={l.id}
                  draggable
                  onDragStart={() => setDragLineId(l.id ?? null)}
                  className={`cursor-grab rounded-lg border px-3 py-2 text-sm ${a ? 'border-primary-200 bg-primary-50' : 'border-neutral-200'} ${hintColor(Number(l.line_total)) ? `border-l-4 ${hintColor(Number(l.line_total))}` : ''}`}>
                  <div className="flex justify-between">
                    <span className="truncate">{l.description}</span>
                    <span className="font-mono text-xs">{formatAmount(Number(l.line_total), currency)}</span>
                  </div>
                  {a && (
                    <button onClick={() => l.id && clearLine(l.id)}
                      className="mt-1 text-[11px] text-primary-600 hover:underline">
                      Assigned - unassign
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* PO lines (drop target) */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">
            PO lines (drop here)
            <span className="ml-2 normal-case font-normal text-neutral-300">same accent color = same amount on both sides (hint only)</span>
          </h4>
          <div className="flex max-h-[55vh] flex-col gap-3 overflow-y-auto pr-1">
            {pos.map((po) => (
              <div key={po.id}>
                <p className="mb-1 font-mono text-xs text-neutral-500">{po.number}</p>
                {po.line_items.map((pl) => {
                  const allocatedHere = Object.entries(assign)
                    .filter(([, t]) => t.poLineId === pl.id)
                    .reduce((s, [lid]) => s + allocatedByLine(lid), 0)
                  return (
                    <div key={pl.id}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => drop(po.id, pl.id)}
                      className={`mb-1 rounded-lg border border-dashed border-neutral-300 px-3 py-2 text-sm hover:border-primary-400 ${hintColor(Number(pl.line_total)) ? `border-l-4 ${hintColor(Number(pl.line_total))}` : ''}`}>
                      <div className="flex justify-between">
                        <span className="truncate">{pl.description}</span>
                        <span className="font-mono text-xs">{formatAmount(Number(pl.line_total), po.currency)}</span>
                      </div>
                      <p className="text-[11px] text-neutral-400">
                        Allocated {formatAmount(allocatedHere, po.currency)} -
                        Remaining {formatAmount(Number(pl.line_total) - allocatedHere, po.currency)}
                      </p>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex justify-end">
        <Button onClick={() => onSubmit(buildAllocations())} disabled={!balanced || submitting}>
          {submitting ? 'Matching...' : 'Confirm allocation & match'}
        </Button>
      </div>
    </div>
  )
}
