import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { formatAmount } from '@/lib/utils'
import type { ApiInvoice, AllocationInput } from '@/services/invoices'
import type { ApiPo } from '@/services/po'

// poId -> amount allocated to that PO from THIS invoice (this session)
type TotalAssign = Record<string, number>

interface Props {
  invoice: ApiInvoice
  pos: ApiPo[]
  onSubmit: (payload: { allocations: AllocationInput[]; nonPoLines: []; referencePoId: null }) => void
  submitting?: boolean
}

export function InvoiceTotalMatchPanel({ invoice, pos, onSubmit, submitting }: Props) {
  const currency = invoice.currency
  const total = Number(invoice.amount)                       // pre-tax
  const anchorLineId = invoice.line_items?.[0]?.id ?? ''
  const [assign, setAssign] = useState<TotalAssign>({})
  const [dragging, setDragging] = useState(false)

  const allocatedSum = useMemo(
    () => Object.values(assign).reduce((s, n) => s + n, 0), [assign])
  const unallocated = total - allocatedSum
  const balanced = Math.abs(unallocated) < 0.01

  const poRemaining = (po: ApiPo) => {
    const otherInvoices = Number(po.already_allocated_total ?? 0)
    const here = assign[po.id] ?? 0
    return Number(po.subtotal) - otherInvoices - here
  }

  const dropOnPo = (po: ApiPo) => {
    setDragging(false)
    if (po.id in assign) return                              // already allocated; edit inline instead
    const remainingBillable = Number(po.subtotal) - Number(po.already_allocated_total ?? 0)
    const amount = Math.max(0, Math.min(unallocated, remainingBillable))
    if (amount <= 0) return
    setAssign((prev) => ({ ...prev, [po.id]: Number(amount.toFixed(2)) }))
  }
  const editAmount = (poId: string, raw: string) => {
    const n = Number(raw)
    setAssign((prev) => ({ ...prev, [poId]: Number.isFinite(n) ? n : 0 }))
  }
  const unassign = (poId: string) =>
    setAssign((prev) => { const n = { ...prev }; delete n[poId]; return n })

  const submit = () => onSubmit({
    allocations: Object.entries(assign).map(([po_id, amount]) => ({
      invoice_line_id: anchorLineId, po_id, po_line_id: null,
      allocated_amount: amount, allocated_tax: 0,
    })),
    nonPoLines: [], referencePoId: null,
  })

  const canSubmit = balanced && Object.keys(assign).length > 0

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-2.5">
        <span className="text-xs text-neutral-500">Unallocated balance (pre-tax)</span>
        <span className={`font-mono text-sm font-semibold ${balanced ? 'text-success-600' : 'text-danger-600'}`}>
          {formatAmount(unallocated, currency)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Whole-invoice drag source */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">Invoice</h4>
          <div
            draggable
            onDragStart={() => setDragging(true)}
            onDragEnd={() => setDragging(false)}
            className="cursor-grab rounded-lg border border-primary-200 bg-primary-50 px-3 py-3 text-sm">
            <div className="flex justify-between">
              <span className="truncate">{invoice.vendor_invoice_number}</span>
              <span className="font-mono text-xs">{formatAmount(total, currency)}</span>
            </div>
            <span className="mt-1 block text-[10px] text-neutral-400">Pretax total · drag onto a PO to match by total amount</span>
          </div>
        </div>

        {/* PO header drop targets */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">Purchase orders (drop here)</h4>
          <div className="flex max-h-[55vh] flex-col gap-2 overflow-y-auto pr-1">
            {pos.map((po) => {
              const here = assign[po.id]
              return (
                <div key={po.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => dropOnPo(po)}
                  className={`rounded-lg border px-3 py-2 text-sm ${here != null ? 'border-primary-300 bg-primary-50' : `border-dashed border-neutral-300 ${dragging ? 'hover:border-primary-400' : ''}`}`}>
                  <div className="flex justify-between">
                    <span className="truncate font-mono text-xs text-neutral-600">{po.number}</span>
                    <span className="font-mono text-xs">{formatAmount(Number(po.subtotal), po.currency)}</span>
                  </div>
                  <p className="text-[11px] text-neutral-400">
                    Subtotal {formatAmount(Number(po.subtotal), po.currency)} · Remaining {formatAmount(poRemaining(po), po.currency)}
                  </p>
                  {here != null && (
                    <div className="mt-1.5 flex items-center gap-2">
                      <span className="text-[11px] text-neutral-500">Allocate</span>
                      <input type="number" step="0.01" value={here}
                        onChange={(e) => editAmount(po.id, e.target.value)}
                        className="h-6 w-28 rounded border border-neutral-300 bg-white px-2 text-[11px] focus:outline-none focus:ring-1 focus:ring-primary-500" />
                      <button onClick={() => unassign(po.id)} className="text-[11px] text-primary-600 hover:underline">unassign</button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <div className="flex justify-end">
        <Button onClick={submit} disabled={!canSubmit || submitting}>
          {submitting ? 'Matching...' : 'Confirm total-amount match'}
        </Button>
      </div>
    </div>
  )
}
