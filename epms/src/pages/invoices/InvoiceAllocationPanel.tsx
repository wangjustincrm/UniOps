import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui/button'
import { formatAmount } from '@/lib/utils'
import type { ApiInvoice, InvoiceLineItem, AllocationInput, NonPoLineInput } from '@/services/invoices'
import type { ApiPo } from '@/services/po'

// invoiceLineId -> { poId, poLineId }
export type AllocationAssignment = Record<string, { poId: string; poLineId: string }>

interface Props {
  invoice: ApiInvoice
  pos: ApiPo[]                 // candidate POs (issued/approved, same vendor)
  onSubmit: (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[]; referencePoId: string | null }) => void
  submitting?: boolean
  defaultAssignments?: AllocationAssignment  // prefill (e.g. AI-recognized PO on upload)
}

export function InvoiceAllocationPanel({ invoice, pos, onSubmit, submitting, defaultAssignments }: Props) {
  const lines: InvoiceLineItem[] = invoice.line_items ?? []
  const [assign, setAssign] = useState<AllocationAssignment>(defaultAssignments ?? {})
  const [dragLineId, setDragLineId] = useState<string | null>(null)
  // Prefill non-PO marks from persisted line_items (re-opening a matched/exception invoice).
  const [nonPo, setNonPo] = useState<Record<string, string | null>>(() => {
    const m: Record<string, string | null> = {}
    for (const l of lines) if (l.id && l.non_po_fee) m[l.id] = l.non_po_note ?? null
    return m
  })
  const [menu, setMenu] = useState<{ x: number; y: number; lineId: string } | null>(null)
  const [referencePoId, setReferencePoId] = useState<string>('')

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
    const poAmounts = new Set(pos.flatMap((po) => po.line_items.map((pl) => key(Number(pl.line_total) - Number(pl.already_allocated ?? 0)))))
    const shared = [...invAmounts].filter((a) => poAmounts.has(a) && Number(a) > 0)
    return new Map(shared.map((a, i) => [a, HINT_COLORS[i % HINT_COLORS.length]]))
  }, [lines, pos])
  const hintColor = (amount: number) => amountColorMap.get(amount.toFixed(2))

  const isNonPo = (lineId?: string | null) => !!lineId && lineId in nonPo
  const excludedTotal = useMemo(
    () => Object.keys(nonPo).reduce((s, lid) => s + allocatedByLine(lid), 0),
    [nonPo, lines],
  )
  const markNonPo = (lineId: string) => {
    clearLine(lineId)                                   // marking wins over any allocation
    setNonPo((p) => ({ ...p, [lineId]: p[lineId] ?? null }))
    setMenu(null)
  }
  const unmarkNonPo = (lineId: string) =>
    setNonPo((p) => { const n = { ...p }; delete n[lineId]; return n })
  const setNote = (lineId: string, note: string) =>
    setNonPo((p) => ({ ...p, [lineId]: note }))

  const unallocated = total - assignedTotal - excludedTotal
  const balanced = Math.abs(unallocated) < 0.01
  const hasAllocations = Object.keys(assign).length > 0
  // Fee-only invoice (nothing allocated to a PO): must link a PO to confirm.
  const needsReferencePo = balanced && !hasAllocations
  const canSubmit = balanced && (hasAllocations || !!referencePoId)

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

  const submit = () => onSubmit({
    allocations: buildAllocations(),
    nonPoLines: Object.entries(nonPo).map(([line_id, note]) => ({ line_id, note })),
    referencePoId: hasAllocations ? null : (referencePoId || null),
  })

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
      {excludedTotal > 0 && (
        <span className="-mt-2 text-[11px] text-neutral-400">
          {formatAmount(excludedTotal, currency)} in non-PO fees excluded from matching
        </span>
      )}

      {!hasAllocations && (
        <div className="flex flex-col gap-1 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5">
          <label className="text-xs font-medium text-amber-800">Link this invoice to a PO</label>
          <span className="text-[11px] text-amber-700">
            This invoice has no PO line allocations. Link it to a purchase order (same vendor) for traceability — the charges are paid via the invoice header.
          </span>
          <select
            value={referencePoId}
            onChange={(e) => setReferencePoId(e.target.value)}
            className="mt-1 h-8 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">Select a purchase order…</option>
            {pos.map((po) => (
              <option key={po.id} value={po.id}>{po.number}</option>
            ))}
          </select>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        {/* Invoice lines (drag source) */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">Invoice lines</h4>
          <div className="flex max-h-[55vh] flex-col gap-2 overflow-y-auto pr-1">
            {lines.map((l) => {
              const a = l.id ? assign[l.id] : undefined
              if (isNonPo(l.id)) {
                return (
                  <div key={l.id}
                    className="rounded-lg border border-neutral-200 bg-neutral-100 px-3 py-2 text-sm opacity-80">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-neutral-500 line-through">{l.description}</span>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="rounded bg-neutral-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-600">
                          Non-PO fee
                        </span>
                        <span className="font-mono text-xs text-neutral-500">{formatAmount(Number(l.line_total), currency)}</span>
                      </div>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      <input
                        type="text"
                        value={(l.id && nonPo[l.id]) || ''}
                        onChange={(e) => l.id && setNote(l.id, e.target.value)}
                        placeholder="note (optional), e.g. shipping"
                        className="h-6 flex-1 rounded border border-neutral-300 bg-white px-2 text-[11px] focus:outline-none focus:ring-1 focus:ring-primary-500"
                      />
                      <button onClick={() => l.id && unmarkNonPo(l.id)}
                        className="text-[11px] text-primary-600 hover:underline">
                        Unmark
                      </button>
                    </div>
                  </div>
                )
              }
              return (
                <div key={l.id}
                  draggable
                  onDragStart={() => setDragLineId(l.id ?? null)}
                  onContextMenu={(e) => { e.preventDefault(); if (l.id) setMenu({ x: e.clientX, y: e.clientY, lineId: l.id }) }}
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
                  <span className="mt-1 block text-[10px] text-neutral-300">right-click to mark as other fee</span>
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
                  const priorAllocated = Number(pl.already_allocated ?? 0)
                  const remaining = Number(pl.line_total) - priorAllocated - allocatedHere
                  return (
                    <div key={pl.id}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => drop(po.id, pl.id)}
                      className={`mb-1 rounded-lg border border-dashed border-neutral-300 px-3 py-2 text-sm hover:border-primary-400 ${hintColor(Number(pl.line_total) - priorAllocated) ? `border-l-4 ${hintColor(Number(pl.line_total) - priorAllocated)}` : ''}`}>
                      <div className="flex justify-between">
                        <span className="truncate">{pl.description}</span>
                        <span className="font-mono text-xs">{formatAmount(Number(pl.line_total), po.currency)}</span>
                      </div>
                      <p className="text-[11px] text-neutral-400">
                        Allocated {formatAmount(priorAllocated + allocatedHere, po.currency)}
                        {priorAllocated > 0 && ` (${formatAmount(priorAllocated, po.currency)} by other invoices)`}
                        {' - '}Remaining {formatAmount(remaining, po.currency)}
                      </p>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-col items-end gap-1">
        {needsReferencePo && !referencePoId && (
          <span className="text-[11px] text-danger-600">Link a PO to confirm a fee-only invoice</span>
        )}
        <Button onClick={submit} disabled={!canSubmit || submitting}>
          {submitting ? 'Matching...' : 'Confirm allocation & match'}
        </Button>
      </div>

      {menu && createPortal(
        <div className="fixed inset-0 z-50"
          onClick={() => setMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setMenu(null) }}>
          <div className="absolute min-w-[210px] rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
            style={{ top: menu.y, left: menu.x }}
            onClick={(e) => e.stopPropagation()}>
            <button
              className="block w-full px-3 py-2 text-left text-sm text-neutral-700 hover:bg-neutral-50"
              onClick={() => markNonPo(menu.lineId)}>
              Mark as other fee (shipping etc.)
            </button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
