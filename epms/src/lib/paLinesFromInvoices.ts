// PA lines implied by the invoices it pays — the browser half of
// epms-api crud/pa_lines.py. The server re-derives on save and its result is
// what gets stored; this copy only lets the screen show those lines up front.
// Keep the two rules identical (paLinesFromInvoices.test.ts pins this one).
import type { ApiInvoice, InvoiceLineItem } from '@/services/invoices'

export interface PoLineLike {
  id: string
  description: string
  unit: string
}

export interface DerivedPaLine {
  po_line_id: string | null
  description: string
  qty: number
  unit: string
  unit_price: number
  line_total: number
  notes: string
}

const FALLBACK_UNIT = 'ea'

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const qtyPrice = (li: InvoiceLineItem | undefined, amount: number): [number, number] => {
  const qty = num(li?.quantity)
  const price = num(li?.unit_price)
  if (qty === null || qty <= 0) return [1, amount]
  return [qty, price ?? Number((amount / qty).toFixed(5))]
}

const note = (inv: ApiInvoice) =>
  inv.vendor_invoice_number ? `${inv.internal_ref} · ${inv.vendor_invoice_number}` : inv.internal_ref

/**
 * null = the lines cannot be derived (any invoice matched only against the PO
 * total, or not matched to these POs at all) and stay hand-picked.
 */
export function derivePaLines(
  invoices: ApiInvoice[],
  poIds: string[],
  poLines: Map<string, PoLineLike>,
): DerivedPaLine[] | null {
  if (invoices.length === 0 || poIds.length === 0) return null
  const poSet = new Set(poIds)
  const plan: { inv: ApiInvoice; mine: NonNullable<ApiInvoice['allocations']> }[] = []
  for (const inv of invoices) {
    const lines = inv.line_items ?? []
    const mine = (inv.allocations ?? []).filter((a) => poSet.has(a.po_id))
    if (mine.length > 0) {
      if (mine.some((a) => a.po_line_id == null)) return null
    } else if (!(inv.po_id && poSet.has(inv.po_id) && lines.length > 0 && lines.every((li) => li.non_po_fee))) {
      return null
    }
    plan.push({ inv, mine })
  }

  const out: DerivedPaLine[] = []
  for (const { inv, mine } of plan) {
    const byInvLine = new Map(mine.map((a) => [String(a.invoice_line_id), a]))
    const feesHere = !!inv.po_id && poSet.has(inv.po_id)
    for (const li of inv.line_items ?? []) {
      const a = byInvLine.get(String(li.id))
      if (a) {
        byInvLine.delete(String(li.id))
        const pl = poLines.get(String(a.po_line_id))
        const amount = Number(a.allocated_amount)
        const [qty, price] = qtyPrice(li, amount)
        out.push({
          po_line_id: a.po_line_id,
          description: (pl?.description ?? li.description ?? '').slice(0, 500) || inv.internal_ref,
          qty, unit: pl?.unit || li.unit || FALLBACK_UNIT, unit_price: price,
          line_total: amount, notes: note(inv),
        })
      } else if (li.non_po_fee && feesHere) {
        const amount = Number(num(li.line_total) ?? 0)
        const [qty, price] = qtyPrice(li, amount)
        const desc = li.non_po_note ? `${li.description || 'Non-PO fee'} — ${li.non_po_note}` : (li.description || 'Non-PO fee')
        out.push({
          po_line_id: null, description: desc.slice(0, 500),
          qty, unit: li.unit || FALLBACK_UNIT, unit_price: price,
          line_total: Number(amount.toFixed(2)), notes: note(inv),
        })
      }
    }
    for (const a of byInvLine.values()) {
      const pl = poLines.get(String(a.po_line_id))
      const amount = Number(a.allocated_amount)
      out.push({
        po_line_id: a.po_line_id,
        description: (pl?.description ?? inv.internal_ref).slice(0, 500),
        qty: 1, unit: pl?.unit || FALLBACK_UNIT, unit_price: amount,
        line_total: amount, notes: note(inv),
      })
    }
  }
  return out.length > 0 ? out : null
}

export const derivedLinesTotal = (lines: DerivedPaLine[]) =>
  Math.round(lines.reduce((s, l) => s + Math.round(l.line_total * 100), 0)) / 100

/**
 * Lines copied from the invoices' own lines — the browser half of
 * crud/pa_lines.py::invoice_lines, used for an agreement PA (no PO lines to
 * derive from; ISS-0109). An invoice whose lines do not add up to its pre-tax
 * amount becomes one line for the whole invoice.
 */
export function invoiceOwnLines(invoices: ApiInvoice[]): DerivedPaLine[] | null {
  const out: DerivedPaLine[] = []
  for (const inv of invoices) {
    const lines = (inv.line_items ?? []).filter((li) => num(li.line_total) !== null)
    const cents = lines.reduce((s, li) => s + Math.round(Number(li.line_total) * 100), 0)
    const amount = Number(inv.amount)
    if (lines.length === 0 || Math.abs(cents - Math.round(amount * 100)) > 1) {
      out.push({
        po_line_id: null, description: `Invoice ${inv.vendor_invoice_number || inv.internal_ref}`.slice(0, 500),
        qty: 1, unit: FALLBACK_UNIT, unit_price: amount, line_total: amount, notes: note(inv),
      })
      continue
    }
    for (const li of lines) {
      const lineTotal = Number(Number(li.line_total).toFixed(2))
      const [qty, price] = qtyPrice(li, lineTotal)
      out.push({
        po_line_id: null, description: (li.description || inv.internal_ref).slice(0, 500),
        qty, unit: li.unit || FALLBACK_UNIT, unit_price: price, line_total: lineTotal, notes: note(inv),
      })
    }
  }
  return out.length > 0 ? out : null
}
