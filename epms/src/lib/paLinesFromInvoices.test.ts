import { describe, expect, it } from 'vitest'
import type { ApiInvoice, InvoiceAllocation, InvoiceLineItem } from '@/services/invoices'
import { derivePaLines, derivedLinesTotal, type PoLineLike } from './paLinesFromInvoices'

// Mirrors epms-api tests/test_pa_lines.py case for case — the two rules must agree.

const PO_A = 'po-a'
const PO_B = 'po-b'
const poLines = new Map<string, PoLineLike>([
  ['pl-1', { id: 'pl-1', description: 'REDUCING UNION', unit: 'pcs' }],
  ['pl-2', { id: 'pl-2', description: 'FOLDING KNIFE', unit: 'pcs' }],
  ['pl-9', { id: 'pl-9', description: 'OTHER PO LINE', unit: 'pcs' }],
])

const line = (id: string, qty: number, price: number, total: number, extra: Partial<InvoiceLineItem> = {}): InvoiceLineItem =>
  ({ id, description: `inv ${id}`, quantity: qty, unit: null, unit_price: price, line_total: total, ...extra })

const alloc = (invLine: string, po: string, poLine: string | null, amount: number): InvoiceAllocation => ({
  id: `a-${invLine}`, invoice_id: 'inv', invoice_line_id: invLine, po_id: po, po_line_id: poLine,
  allocated_amount: amount, allocated_tax: 0, allocated_total: amount, variance: null, variance_pct: null, note: null,
})

const invoice = (over: Partial<ApiInvoice>): ApiInvoice =>
  ({ id: 'inv', internal_ref: 'INV-1', vendor_invoice_number: 'V1', po_id: PO_A, line_items: [], allocations: [], ...over }) as ApiInvoice

describe('derivePaLines', () => {
  it('builds one line per line-level allocation, worth exactly the allocated amount', () => {
    const inv = invoice({
      line_items: [line('l1', 10, 19.421, 194.21), line('l2', 2, 28.25, 56.5)],
      allocations: [alloc('l1', PO_A, 'pl-1', 194.21), alloc('l2', PO_A, 'pl-2', 56.5)],
    })
    const out = derivePaLines([inv], [PO_A], poLines)!
    expect(out.map((l) => [l.po_line_id, l.description, l.qty, l.unit_price, l.line_total])).toEqual([
      ['pl-1', 'REDUCING UNION', 10, 19.421, 194.21],
      ['pl-2', 'FOLDING KNIFE', 2, 28.25, 56.5],
    ])
    expect(out[0].notes).toBe('INV-1 · V1')
    expect(derivedLinesTotal(out)).toBe(250.71)
  })

  it('returns null when any invoice was matched only against the PO total', () => {
    const lineLevel = invoice({ id: 'i1', line_items: [line('l1', 1, 5, 5)], allocations: [alloc('l1', PO_A, 'pl-1', 5)] })
    const header = invoice({ id: 'i2', line_items: [line('h1', 1, 9, 9)], allocations: [alloc('h1', PO_A, null, 9)] })
    expect(derivePaLines([lineLevel], [PO_A], poLines)).not.toBeNull()
    expect(derivePaLines([lineLevel, header], [PO_A], poLines)).toBeNull()
  })

  it("takes only this PA's share of an invoice split across POs", () => {
    const inv = invoice({
      line_items: [line('l1', 1, 125, 125), line('l2', 1, 242, 242)],
      allocations: [alloc('l1', PO_A, 'pl-1', 125), alloc('l2', PO_B, 'pl-9', 242)],
    })
    expect(derivePaLines([inv], [PO_B], poLines)!.map((l) => l.line_total)).toEqual([242])
    expect(derivePaLines([inv], [PO_A], poLines)!.map((l) => l.line_total)).toEqual([125])
  })

  it('puts a non-PO fee on the header PO\'s PA only', () => {
    const inv = invoice({
      po_id: PO_A,
      line_items: [line('l1', 1, 100, 100), line('f1', 1, 33.79, 33.79, { non_po_fee: true, description: 'FREIGHT' }),
                   line('l2', 1, 50, 50)],
      allocations: [alloc('l1', PO_A, 'pl-1', 100), alloc('l2', PO_B, 'pl-9', 50)],
    })
    const onA = derivePaLines([inv], [PO_A], poLines)!
    expect(onA.map((l) => [l.po_line_id, l.line_total])).toEqual([['pl-1', 100], [null, 33.79]])
    expect(derivePaLines([inv], [PO_B], poLines)!.map((l) => l.line_total)).toEqual([50])
  })

  it('derives a fee-only invoice headed on the PA\'s PO, and refuses one that is not', () => {
    const fee = invoice({ po_id: PO_A, line_items: [line('f1', 1, 40, 40, { non_po_fee: true })], allocations: [] })
    expect(derivePaLines([fee], [PO_A], poLines)!.map((l) => [l.po_line_id, l.line_total])).toEqual([[null, 40]])
    expect(derivePaLines([fee], [PO_B], poLines)).toBeNull()
  })

  it('returns null with no invoices — the GR-only / manual PA keeps hand-picked lines', () => {
    expect(derivePaLines([], [PO_A], poLines)).toBeNull()
  })

  it('falls back to qty 1 at the allocated amount when the invoice line has no quantity', () => {
    const inv = invoice({ line_items: [line('l1', 0, 0, 80)], allocations: [alloc('l1', PO_A, 'pl-1', 80)] })
    expect(derivePaLines([inv], [PO_A], poLines)!.map((l) => [l.qty, l.unit_price, l.line_total])).toEqual([[1, 80, 80]])
  })
})
