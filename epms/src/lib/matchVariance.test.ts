import { describe, expect, it } from 'vitest'
import { matchMode, buildLineComparisons, feeLines, feeLineTotal, receiptSummary } from './matchVariance'
import type { ApiInvoice, InvoiceAllocation } from '@/services/invoices'
import type { ApiPoLineItem } from '@/services/po'

// ── Fixture builders ────────────────────────────────────────────────────────
// Minimal literal objects, not imports from production fixtures (brief
// requirement) — only the fields buildLineComparisons/matchMode actually read
// are filled in; everything else on ApiInvoice/InvoiceAllocation/ApiPoLineItem
// is cast away with `as unknown as` to keep the fixtures short.

function makeAllocation(overrides: Partial<InvoiceAllocation>): InvoiceAllocation {
  return {
    id: 'alloc-1',
    invoice_id: 'inv-1',
    invoice_line_id: 'line-1',
    po_id: 'po-1',
    po_line_id: 'pol-1',
    allocated_amount: 1000,
    allocated_tax: 0,
    allocated_total: 1000,
    variance: null,
    variance_pct: null,
    note: null,
    ...overrides,
  }
}

function makeInvoice(overrides: Partial<ApiInvoice>): ApiInvoice {
  return {
    id: 'inv-1',
    internal_ref: 'INV-0001',
    vendor_invoice_number: 'V-1',
    vendor_id: 'vendor-1',
    vendor_name: 'Acme',
    amount: 1000,
    tax_amount: 0,
    total_amount: 1000,
    currency: 'CAD',
    invoice_date: '2026-08-01',
    due_date: '2026-09-01',
    line_items: [
      { id: 'line-1', description: 'Widget', quantity: 10, unit: 'ea', unit_price: 100, line_total: 1000 },
    ],
    status: 'matched',
    uploaded_at: '2026-08-01T00:00:00Z',
    uploaded_by: 'user-1',
    created_at: '2026-08-01T00:00:00Z',
    allocations: [],
    ...overrides,
  } as unknown as ApiInvoice
}

function makePoLine(overrides: Partial<ApiPoLineItem>): ApiPoLineItem {
  return {
    id: 'pol-1',
    description: 'Widget PO Line',
    qty: 10,
    unit: 'ea',
    unit_price: 95,
    line_total: 950,
    received_qty: 10,
    ...overrides,
  }
}

describe('matchMode', () => {
  it('returns by-line when every allocation carries a non-null po_line_id', () => {
    const allocations = [
      makeAllocation({ id: 'a1', po_line_id: 'pol-1' }),
      makeAllocation({ id: 'a2', po_line_id: 'pol-2' }),
    ]
    expect(matchMode(allocations)).toBe('by-line')
  })

  it('returns by-amount when allocations have po_line_id === null', () => {
    const allocations = [
      makeAllocation({ id: 'a1', po_line_id: null }),
      makeAllocation({ id: 'a2', po_line_id: null }),
    ]
    expect(matchMode(allocations)).toBe('by-amount')
  })

  it('returns by-amount when even one allocation among several lacks a po_line_id (mixed is not by-line)', () => {
    const allocations = [
      makeAllocation({ id: 'a1', po_line_id: 'pol-1' }),
      makeAllocation({ id: 'a2', po_line_id: null }),
    ]
    expect(matchMode(allocations)).toBe('by-amount')
  })

  it('returns by-amount for an empty allocations array (unreachable through the panel — it short-circuits on allocations.length === 0 — but the function is exported and must not throw or default to by-line)', () => {
    expect(matchMode([])).toBe('by-amount')
  })
})

describe('buildLineComparisons', () => {
  it('joins invoice line -> allocation -> PO line and computes a per-row variance', () => {
    const invoice = makeInvoice({
      allocations: [makeAllocation({ id: 'a1', invoice_line_id: 'line-1', po_line_id: 'pol-1', allocated_amount: 1000 })],
    })
    const poLines = [makePoLine({ id: 'pol-1', line_total: 950 })]

    const rows = buildLineComparisons(invoice, poLines)

    expect(rows).toHaveLength(1)
    expect(rows[0]).toEqual({
      allocationId: 'a1',
      invoiceLineDescription: 'Widget',
      invoiceQty: 10,
      invoiceUnitPrice: 100,
      invoiceAmount: 1000,
      poId: 'po-1',
      poNumber: null,   // makeAllocation's default fixture carries no po_number
      poLineResolved: true,
      poLineDescription: 'Widget PO Line',
      poQty: 10,
      poUnitPrice: 95,
      poAmount: 950,
      variance: 50, // 1000 - 950, since the allocation carries no backend variance
    })
  })

  it('coerces a Decimal-as-string allocated_amount with Number() before arithmetic', () => {
    const invoice = makeInvoice({
      allocations: [
        makeAllocation({
          id: 'a1',
          invoice_line_id: 'line-1',
          po_line_id: 'pol-1',
          // Simulates the real wire shape: Pydantic serialises Decimal as a
          // JSON string. Cast past the (number) type to model that payload.
          allocated_amount: '900.00' as unknown as number,
        }),
      ],
    })
    const poLines = [makePoLine({ id: 'pol-1', line_total: 1000 })]

    const rows = buildLineComparisons(invoice, poLines)

    // If this concatenated instead of adding, the result would be the
    // string "900.001000" or NaN — assert the real arithmetic result.
    expect(rows[0].invoiceAmount).toBe(900)
    expect(rows[0].poAmount).toBe(1000)
    expect(rows[0].variance).toBe(-100)
  })

  it('yields poAmount: null (not a throw) when the allocation\'s po_line_id is absent from poLines', () => {
    const invoice = makeInvoice({
      allocations: [
        makeAllocation({ id: 'a1', invoice_line_id: 'line-1', po_line_id: 'pol-missing', allocated_amount: 500 }),
      ],
    })
    const poLines = [makePoLine({ id: 'pol-1' })] // does not contain 'pol-missing'

    expect(() => buildLineComparisons(invoice, poLines)).not.toThrow()
    const rows = buildLineComparisons(invoice, poLines)
    expect(rows[0].poAmount).toBeNull()
    expect(rows[0].poLineDescription).toBeNull()
    expect(rows[0].poLineResolved).toBe(false)
    // No PO line to compare against — falls back to the invoice amount
    // rather than fabricating a zero/matched variance.
    expect(rows[0].variance).toBe(500)
  })

  it('prefers the backend-computed allocation.variance when present, over local arithmetic', () => {
    const invoice = makeInvoice({
      allocations: [
        makeAllocation({
          id: 'a1', invoice_line_id: 'line-1', po_line_id: 'pol-1', allocated_amount: 1000,
          variance: '25.00' as unknown as number,
        }),
      ],
    })
    const poLines = [makePoLine({ id: 'pol-1', line_total: 950 })]

    const rows = buildLineComparisons(invoice, poLines)
    expect(rows[0].variance).toBe(25)
  })
})

describe('feeLines / feeLineTotal', () => {
  it('returns only the lines flagged non_po_fee, with description and amount', () => {
    const invoice = makeInvoice({
      line_items: [
        { id: 'line-1', description: 'Widget', quantity: 10, unit: 'ea', unit_price: 100, line_total: 1000, non_po_fee: false },
        { id: 'line-2', description: 'Shipping', quantity: 1, unit: 'ea', unit_price: 50, line_total: 50, non_po_fee: true },
      ],
    })

    expect(feeLines(invoice)).toEqual([{ description: 'Shipping', amount: 50 }])
  })

  it('returns an empty array when no line is flagged non_po_fee', () => {
    const invoice = makeInvoice({
      line_items: [
        { id: 'line-1', description: 'Widget', quantity: 10, unit: 'ea', unit_price: 100, line_total: 1000, non_po_fee: false },
      ],
    })

    expect(feeLines(invoice)).toEqual([])
    expect(feeLineTotal(invoice)).toBe(0)
  })

  it('coerces a Decimal-as-string line_total with Number() and sums correctly across multiple fee lines', () => {
    const invoice = makeInvoice({
      line_items: [
        { id: 'line-1', description: 'Widget', quantity: 10, unit: 'ea', unit_price: 100, line_total: 1000, non_po_fee: false },
        // Simulates the real wire shape: Pydantic serialises Decimal as a
        // JSON string.
        { id: 'line-2', description: 'Shipping', quantity: 1, unit: 'ea', unit_price: 50, line_total: '50.00' as unknown as number, non_po_fee: true },
        { id: 'line-3', description: 'Packaging', quantity: 1, unit: 'ea', unit_price: 12.5, line_total: '12.50' as unknown as number, non_po_fee: true },
      ],
    })

    // If this concatenated instead of adding, the total would be the string
    // "50.0012.50" or NaN — assert the real arithmetic result.
    expect(feeLineTotal(invoice)).toBe(62.5)
  })
})

describe('receiptSummary', () => {
  const inv = (total: string, receipts: Array<string | null>) => ({
    total_amount: total,
    claimed_receipts: receipts.map((total_amount, i) => ({
      id: `r${i}`, receipt_ref: `R${i}`, receipt_date: '2026-08-01',
      receipt_type: total_amount === null ? 'delivery' : 'counter_slip',
      total_amount, vendor_name: null,
    })),
  }) as never

  it('excludes receipts that carry no amount', () => {
    const s = receiptSummary(inv('120.00', ['120.00', null]))
    expect(s.pricedCount).toBe(1)
    expect(s.receiptTotal).toBe(120)
    expect(s.hasVariance).toBe(false)
  })

  it('reports zero variance when no priced receipt is claimed', () => {
    const s = receiptSummary(inv('120.00', [null, null]))
    expect(s.hasVariance).toBe(false)
    // 关键:绝不能得出「差异 = 整张发票 120」这种误报
    expect(s.variance).toBe(0)
  })

  it('compares against the tax-INCLUSIVE invoice total', () => {
    // total_amount 是含税总额 —— 与 PO 分摊路线的税前口径**相反**,这是对的。
    // Whole-branch review (finding 8b): amount/tax_amount are given real
    // values DIFFERENT from total_amount here — the previous fixture omitted
    // `amount` entirely, so a regression reading invoice.amount instead of
    // invoice.total_amount produced Number(undefined) = NaN, and the
    // assertion happened to pass or fail on NaN-comparison quirks rather
    // than on an honest numeric mismatch. With amount=100/tax=13 vs
    // total=113, a wrong-field read would compare 113 against 100 and fail
    // for a real, legible reason.
    const s = receiptSummary({
      amount: '100.00',
      tax_amount: '13.00',
      total_amount: '113.00',
      claimed_receipts: [{
        id: 'r0', receipt_ref: 'R0', receipt_date: '2026-08-01',
        receipt_type: 'counter_slip', total_amount: '113.00', vendor_name: null,
      }],
    } as never)
    expect(s.hasVariance).toBe(false)
  })

  it('absorbs sub-cent float noise', () => {
    const s = receiptSummary(inv('100.00', ['19.99', '0.01', '80.00']))
    expect(s.hasVariance).toBe(false)
  })

  it('flags a genuine difference', () => {
    const s = receiptSummary(inv('120.00', ['100.00']))
    expect(s.hasVariance).toBe(true)
    expect(s.variance).toBeCloseTo(-20, 2)
  })
})
